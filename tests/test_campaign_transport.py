import base64
import copy
import io
import json
from pathlib import Path
import socket
import sys
import threading
from types import SimpleNamespace
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


@pytest.mark.parametrize("wall_skew", [-50, 50])
@pytest.mark.parametrize("reply_delay", [0, 5, 90])
def test_cross_host_deadline_ignores_wall_skew_and_counts_reply_transit(tmp_path, monkeypatch, wall_skew, reply_delay):
    import qwen_campaign as C
    host = [700 + reply_delay]
    client = [12345]
    monkeypatch.setattr(CI, "time", SimpleNamespace(time=lambda:1000 + wall_skew, monotonic=lambda:client[0]))
    monkeypatch.setattr(C, "time", SimpleNamespace(time=lambda:1000, monotonic=lambda:host[0]))
    ledger = C.Ledger.create(tmp_path / "ledger.json", "synthetic")
    configuration = {"policy":"selective", "output_tokens":600, "api_enabled":True}
    run = ledger.register("baseline", "a" * 40, "b" * 64, configuration, ["a"])
    gateway = C.Gateway(ledger, "synthetic", "t" * 32, "baseline", run, tmp_path / "pricing.json")
    monkeypatch.setattr(C, "verify_prices", lambda _:None)
    timeouts = []
    def paid(*args, **kwargs):
        timeouts.append(kwargs["timeout"])
        return {"usage":{"cost":.001}}
    monkeypatch.setattr(C, "remote_json", paid)
    remote = CI.Remote("https://synthetic.trycloudflare.com", "t" * 32)
    remote.set_clock_reference({"accepted":True, "gateway_monotonic":700})
    class Opener:
        def open(self, request, timeout):
            result = gateway.call(request.get_header("Authorization"), request.get_header("X-eval-question"),
                                  request.data, deadline=float(request.get_header("X-eval-deadline")))
            return io.BytesIO(json.dumps(result).encode())
    remote.opener = Opener()
    p, _ = proxy(); p.remote = remote
    p.handle("/begin", b'{"id":"a"}')
    assert p.deadline == 12435  # unrelated client monotonic origin
    if reply_delay >= 90:
        with pytest.raises(C.CampaignError, match="expired"):
            p.handle("/chat", request_body())
        assert ledger.totals()["attempts"] == 0 and not timeouts
    else:
        p.handle("/chat", request_body())
        assert timeouts == [min(85, 90 - reply_delay)]
        assert ledger.totals()["actual_usd"] == "0.001"


@pytest.mark.parametrize("host", [None, True, "700", float("nan"), float("inf")])
def test_missing_or_nonfinite_clock_reference_rejects_before_transfer(host):
    remote = CI.Remote("https://synthetic.trycloudflare.com", "synthetic")
    with pytest.raises(ValueError):
        remote.mapped_deadline(100)
    with pytest.raises(ValueError):
        remote.set_clock_reference({"accepted":True, "gateway_monotonic":host})


def test_freeze_and_each_start_replace_clock_reference_after_reply(monkeypatch):
    clock = [12345]
    monkeypatch.setattr(CI, "time", SimpleNamespace(monotonic=lambda:clock[0]))
    remote = CI.Remote("https://synthetic.trycloudflare.com", "synthetic")
    replies = iter([700, 800, 900])
    monkeypatch.setattr(remote, "request", lambda *a, **kw: json.dumps(
        {"accepted":True, "gateway_monotonic":next(replies)}).encode())
    for route, host in [("/freeze",700), ("/start/0",800), ("/start/1",900)]:
        clock[0] += 101  # independent rate/elapsed time is discarded each batch
        remote.post(route)
        assert remote.mapped_deadline(clock[0] + 90) == host + 90
    for deadline in [True, float("nan"), float("inf")]:
        with pytest.raises(ValueError):
            remote.mapped_deadline(deadline)


def test_start_acknowledgment_samples_gateway_clock(tmp_path, monkeypatch):
    import campaign_local_server as S
    session = object.__new__(S.Session)
    session.freeze = {}; session.gateways = {0:object()}
    session.lock = threading.RLock(); session.active = None; session.next_run = 0; session.root = tmp_path
    monkeypatch.setattr(S, "time", SimpleNamespace(monotonic=lambda:700))
    assert session.post("/start/0", b"{}", "synthetic", None) == {"accepted":True, "gateway_monotonic":700}


def proxy():
    calls = []
    class Remote:
        def post(self,*args,**kwargs):
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


def test_public_packet_excludes_goldens_and_preserves_ledger_identity(tmp_path,monkeypatch):
    import prepare_campaign_transport as P
    import qwen_campaign as C
    data=tmp_path/'data';(data/'xlsx_files').mkdir(parents=True)
    (data/'queries.json').write_text(json.dumps([{'id':'opaque','query':'Synthetic','file_name':'book.xlsx'}]))
    (data/'xlsx_files/book.xlsx').write_bytes(b'synthetic workbook')
    (data/'answers.json').write_text('{"opaque":"NEVER COPY THIS"}')
    archive=tmp_path/'source.zip'
    archive.write_bytes(zip_bytes([('SOURCE_MANIFEST.json',json.dumps({'source_commit':'a'*40,'files':{}}).encode())]))
    private={}
    monkeypatch.setattr(P,'save_private',lambda path,raw:private.update(json.loads(raw)))
    configuration={'policy':'fallback','output_tokens':600,'api_enabled':True}
    spec={'stage':'baseline','split':'public','frozen_benchmark_manifest_sha256':'b'*64,
          'sources':{'candidate':str(archive)},'datasets':[{'label':'synthetic','path':str(data)}],
          'configurations':{'candidate':configuration}}
    out=tmp_path/'transport';P.prepare(spec,out,'c'*40)
    manifest=json.loads((out/'manifest.json').read_bytes());batch=manifest['batches'][0]
    clear=W.decrypt((out/batch['cipher_file']).read_bytes(),base64.b64decode(private['input_key']),batch['aad'])
    assert b'NEVER COPY THIS' not in clear and set(batch['files'])=={'queries.json','xlsx_files/book.xlsx'}
    ledger=C.Ledger.create(tmp_path/'ledger.json','synthetic')
    expected=ledger.register('baseline','a'*40,batch['input_sha256'],configuration,batch['ids'])
    assert expected==manifest['runs'][0]['invocation']
