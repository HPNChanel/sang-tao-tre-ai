import base64
import copy
import io
import json
from pathlib import Path
import socket
import sys
import threading
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import campaign_wire as W
import ci_qwen_campaign as CI


def zip_bytes(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output,"w") as archive:
        for name, data in entries:
            # ZipInfo normally normalizes Windows separators at construction.
            # Set the raw member name afterwards to exercise hostile archives.
            entry=zipfile.ZipInfo(); entry.filename=name
            archive.writestr(entry, data)
    return output.getvalue()


@pytest.mark.parametrize("name", ["../outside", "/outside", "x/../../outside", "x\\outside", "C:/outside"])
def test_archive_rejects_escaping_paths(tmp_path, name):
    with pytest.raises(ValueError):
        W.unpack(zip_bytes([(name,b"secret")]), tmp_path / "unpacked")
    assert not (tmp_path / "unpacked").exists()


def test_archive_inventory_is_exact_and_integrity_checked(tmp_path):
    archive = zip_bytes([("queries.json",b"[]")])
    with pytest.raises(ValueError):
        W.unpack(archive, tmp_path / "extra", {"queries.json":W.digest(b"[]"),"answer.json":"bad"})
    with pytest.raises(ValueError):
        W.unpack(archive, tmp_path / "changed", {"queries.json":W.digest(b"different")})
    W.unpack(archive, tmp_path / "valid", {"queries.json":W.digest(b"[]")})
    assert (tmp_path / "valid/queries.json").read_bytes() == b"[]"


def test_cipher_authenticates_metadata_and_content():
    pytest.importorskip("cryptography")
    key, metadata = bytes(range(32)), {"source":"a", "batch":4}
    encrypted = W.encrypt(b"private results", key, metadata)
    assert b"private results" not in encrypted
    assert W.decrypt(encrypted,key,metadata,"RESULT") == b"private results"
    with pytest.raises(Exception):
        W.decrypt(encrypted,key,dict(metadata,batch=5),"RESULT")
    with pytest.raises(Exception):
        W.decrypt(encrypted[:-1]+bytes([encrypted[-1]^1]),key,metadata,"RESULT")
    assert W.encrypt(b"private results",key,metadata) != encrypted


@pytest.mark.parametrize("url", ["http://x.trycloudflare.com", "https://elsewhere.test", "https://x.trycloudflare.com/path", "https://x.trycloudflare.com@elsewhere.test", "https://x.trycloudflare.com/?token=private"])
def test_remote_requires_one_tls_origin(url):
    with pytest.raises(ValueError):
        CI.Remote(url,"synthetic-token")


def proxy():
    calls = []
    class Remote:
        def post(self,*args):
            calls.append(args)
            return {"usage":{"cost":.001}}
    p = object.__new__(CI.ScopedProxy)
    p.remote, p.run = Remote(), {"number":2,"configuration":{"api_enabled":True,"output_tokens":600}}
    p.ids, p.current, p.position, p.attempts = ["a","b"], None, -1, 0
    p.queries = {"a":"synthetic table question", "b":"synthetic table question"}
    p.serial = threading.RLock()
    return p, calls


def request_body():
    return json.dumps({"model":"qwen/qwen3.7-flash", "max_tokens":600, "temperature":0,
        "response_format":{"type":"json_object"},"reasoning":{"enabled":False},
        "messages":[{"role":"system","content":"JSON only"},
                    {"role":"user","content":"PLAN SCHEMA:\n{}\n\nQUESTION:\nsynthetic table question\n"}]}).encode()


def test_proxy_owns_case_identity_and_three_attempts():
    p,calls = proxy()
    with pytest.raises(ValueError):
        p.handle("/begin",b'{"id":"b"}')
    with pytest.raises(ValueError):
        p.handle("/chat",request_body())
    p.handle("/begin",b'{"id":"a"}')
    for _ in range(3):
        p.handle("/chat",request_body())
    with pytest.raises(ValueError):
        p.handle("/chat",request_body())
    assert len(calls)==3 and all(c[2]=="a" for c in calls)
    assert all(c[0]=="/chat/2" for c in calls)
    p.handle("/end",b'{}')
    with pytest.raises(ValueError):
        p.handle("/begin",b'{"id":"a"}')
    p.handle("/begin",b'{"id":"b"}')
    p.handle("/chat",request_body())
    assert calls[-1][2] == "b"


def test_proxy_rejects_prompt_for_another_case():
    p,calls = proxy(); p.handle("/begin",b'{"id":"a"}')
    body=json.loads(request_body()); body["messages"][1]["content"] += "and another question"
    with pytest.raises(ValueError):
        p.handle("/chat",json.dumps(body).encode())
    body["messages"][1]["content"] = "QUESTION:\nunrelated"
    with pytest.raises(ValueError):
        p.handle("/chat",json.dumps(body).encode())
    assert not calls


@pytest.mark.parametrize("route,raw", [("/arbitrary",b'{}'),("/begin",b'{"id":"unknown"}')])
def test_proxy_rejects_other_routes_and_cases(route,raw):
    p,calls = proxy()
    with pytest.raises(ValueError):
        p.handle(route,raw)
    assert not calls


def test_proxy_enforces_frozen_cap_without_dispatch():
    p,calls = proxy(); p.handle("/begin",b'{"id":"a"}')
    body=json.loads(request_body()); body["max_tokens"]=1200
    with pytest.raises(ValueError):
        p.handle("/chat",json.dumps(body).encode())
    assert not calls


def test_container_flags_enforce_resource_and_network_boundary(tmp_path):
    args=CI.docker_base("test","sha256:fake",tmp_path,tmp_path,tmp_path)
    for expected in ("--network=none","--memory=1000000000","--memory-swap=1000000000",
                     "--user=65534:65534","--cap-drop=ALL","--security-opt=no-new-privileges","--read-only"):
        assert expected in args
    assert not any("TOKEN" in x or "KEY" in x for x in args)


def test_completed_receipts_recover_without_replaying_or_changing_identity(tmp_path):
    from campaign_local_server import Session
    key=bytes(range(32)); session=object.__new__(Session)
    session.root=tmp_path; (tmp_path/'received').mkdir()
    session.raw=b'frozen-manifest'; session.secret={'output_key':base64.b64encode(key).decode()}
    run={'number':0,'invocation':'unchanged','label':'candidate','batch_index':0,'configuration':{'policy':'fallback','output_tokens':600,'api_enabled':True}}
    batch={'input_sha256':'input'}; source={'sha':'source'}
    session.manifest={'runs':[run,dict(run,number=1)],'sources':{'candidate':source},'batches':[batch]}
    session.freeze={'manifest_sha256':W.digest(session.raw),'images':{'candidate':{'image_id':'image'}}}
    (tmp_path/'environment-freeze.json').write_text(json.dumps(session.freeze))
    meta=W.result_metadata(W.digest(session.raw),run,batch,source,'image')
    (tmp_path/'received/run-000.aesgcm').write_bytes(W.encrypt(b'private ZIP',key,meta))
    session.freeze=None; session.next_run=0; session.recover()
    assert session.next_run==1 and session.manifest['runs'][0]['invocation']=='unchanged'
    (tmp_path/'received/run-002.aesgcm').write_bytes(b'gap')
    with pytest.raises(ValueError):
        session.recover()
