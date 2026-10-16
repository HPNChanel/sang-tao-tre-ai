"""Prepare answer-free public/sealed transport outside the source repository."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import time
import zipfile

import campaign_wire as W
from campaign_local_server import load_private, save_private
import qwen_campaign as C


def identity(source, batch, configuration, repetition=1):
    hashes = [hashlib.sha256(q.encode()).hexdigest() for q in batch["ids"]]
    value = {"source_sha":source, "input_sha256":batch["input_sha256"],
             "configuration":configuration, "repetition":repetition,
             "case_ids_sha256":hashlib.sha256(C.canonical(hashes)).hexdigest()}
    return hashlib.sha256(C.canonical(value)).hexdigest()


def prepare(spec, output, harness_sha):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("refusing to overwrite a transport packet")
    output.mkdir(parents=True)
    (output / "shards").mkdir()
    secret = {"token":secrets.token_urlsafe(32), "input_key":base64.b64encode(secrets.token_bytes(32)).decode(),
              "output_key":base64.b64encode(secrets.token_bytes(32)).decode()}
    manifest = {"schema_version":1, "harness_sha":harness_sha, "stage":spec["stage"],
                "split":spec["split"], "expires_unix":time.time() + 8*3600,
                "sources":{}, "batches":[], "runs":[],
                "frozen_benchmark_manifest_sha256":spec["frozen_benchmark_manifest_sha256"]}
    for label, archive_path in spec["sources"].items():
        if label not in {"baseline","candidate"}:
            raise ValueError("unknown source label")
        raw = Path(archive_path).read_bytes()
        with zipfile.ZipFile(Path(archive_path)) as archive:
            source = json.loads(archive.read("SOURCE_MANIFEST.json"))["source_commit"]
        name = "source-" + label + ".zip"
        (output / name).write_bytes(raw)
        manifest["sources"][label] = {"sha":source, "archive":name, "archive_sha256":W.digest(raw)}
    if spec.get("sealed_transport_manifest"):
        transfer_path=Path(spec["sealed_transport_manifest"])
        transfer=json.loads(transfer_path.read_text())
        if transfer["frozen_benchmark_manifest_sha256"] != manifest["frozen_benchmark_manifest_sha256"]:
            raise ValueError("sealed benchmark freeze differs")
        raw_key=load_private(spec["sealed_transport_key"])
        if len(raw_key)!=32:
            raise ValueError("invalid sealed input key")
        secret["input_key"]=base64.b64encode(raw_key).decode()
        for item in transfer["batches"]:
            index=item["index"]
            aad={"schema_version":1,"index":index,
                 "frozen_benchmark_manifest_sha256":manifest["frozen_benchmark_manifest_sha256"],
                 "input_sha256":item["input_sha256"]}
            if W.digest(b'STAI-SHARD-1\n'+W.canonical(aad)) != item["aad_sha256"]:
                raise ValueError("sealed authenticated metadata differs")
            encrypted=(transfer_path.parent/item["encrypted_file"]).read_bytes()
            if W.digest(encrypted)!=item["encrypted_sha256"]:
                raise ValueError("sealed ciphertext differs")
            target=f"shards/batch-{index:03}.aesgcm"; (output/target).write_bytes(encrypted)
            manifest["batches"].append({"index":index,"ids":item["ordered_ids"],"files":item["file_sha256"],
                "input_sha256":item["input_sha256"],"cipher_file":target,"cipher_sha256":W.digest(encrypted),
                "aad":aad,"order":item["order"]})
    else:
        for dataset in spec["datasets"]:
            folder=Path(dataset["path"])
            queries=json.loads((folder/'queries.json').read_text(encoding='utf-8-sig'))
            for offset in range(0,len(queries),20):
                batch=queries[offset:offset+20]; index=len(manifest["batches"])
                with tempfile.TemporaryDirectory(prefix='stai-public-input-') as temp:
                    inputs=Path(temp); (inputs/'xlsx_files').mkdir()
                    C.atomic_json(inputs/'queries.json',batch)
                    for name in {q['file_name'] for q in batch}:
                        if Path(name).name!=name or not name.lower().endswith('.xlsx') or ':' in name:
                            raise ValueError('unsafe workbook name')
                        shutil.copyfile(folder/'xlsx_files'/name,inputs/'xlsx_files'/name)
                    files=W.snapshot(inputs); input_sha=W.input_digest(files)
                    aad={'schema_version':1,'index':index,
                         'frozen_benchmark_manifest_sha256':manifest['frozen_benchmark_manifest_sha256'],
                         'input_sha256':input_sha}
                    encrypted=W.encrypt(W.pack(inputs),base64.b64decode(secret['input_key']),aad,'SHARD')
                target=f'shards/batch-{index:03}.aesgcm';(output/target).write_bytes(encrypted)
                manifest['batches'].append({'index':index,'ids':[str(q['id']) for q in batch],
                    'files':files,'input_sha256':input_sha,'aad':aad,'cipher_file':target,
                    'cipher_sha256':W.digest(encrypted),'dataset':dataset['label']})
    for batch in manifest['batches']:
        order=batch.get('order',spec.get('order',['candidate']))
        if order in ('AB','BA'):
            order=['baseline','candidate'] if order=='AB' else ['candidate','baseline']
        for label in order:
            configuration=spec['configurations'][label]
            repetition=spec.get('repetition',1)
            run={'number':len(manifest['runs']),'batch_index':batch['index'],'label':label,
                 'configuration':configuration,'repetition':repetition}
            run['invocation']=identity(manifest['sources'][label]['sha'],batch,configuration,repetition)
            manifest['runs'].append(run)
    W.validate_manifest(manifest)
    raw=W.canonical(manifest)
    (output/'manifest.json').write_bytes(raw)
    save_private(output/'transport-secret.dpapi',W.canonical(secret))
    return {'manifest_sha256':W.digest(raw),'batches':len(manifest['batches']),
            'runs':len(manifest['runs']),'harness_sha':harness_sha}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--spec');parser.add_argument('--output',required=True)
    parser.add_argument('--harness-sha');parser.add_argument('--install-secret',action='store_true')
    args=parser.parse_args()
    if args.install_secret:
        raw=load_private(Path(args.output)/'transport-secret.dpapi')
        result=subprocess.run(['gh','secret','set','STAI_EVAL_SECRET','--repo','HPNChanel/sang-tao-tre-ai'],
                              input=raw,capture_output=True,check=False)
        if result.returncode:
            raise SystemExit('temporary transport secret installation failed')
        print('Temporary transport secret installed; no provider key transferred.')
    else:
        print(json.dumps(prepare(json.loads(Path(args.spec).read_text(encoding='utf-8-sig')),args.output,args.harness_sha)))


if __name__=='__main__':
    main()
