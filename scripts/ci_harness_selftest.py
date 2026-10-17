"""Exercise the Linux transport boundary without a provider key or paid call."""
import json
from pathlib import Path
import subprocess
import tempfile
import threading

from ci_docker_smoke import IMAGE
from ci_qwen_campaign import ROOT, ScopedProxy, bounded_output, docker_base


def resource_checks(work, inputs, sockets):
    context=work/'test-image';context.mkdir()
    (context/'Dockerfile').write_text('FROM '+IMAGE+'\nRUN pip install --no-cache-dir pytest==9.1.1\n')
    built=subprocess.run(['docker','build','-t','stai-fault-tests:local',str(context)],capture_output=True,text=True,timeout=180)
    assert built.returncode==0,'fault-test image build failed'
    with bounded_output(work/'fault-output'):
        command=docker_base('stai-fault-probe','stai-fault-tests:local',inputs,work/'fault-output',sockets)
        # Add test files before the image positional argument. The original
        # candidate run.py/src and locked runtime dependencies remain intact.
        position=command.index('--entrypoint=python')
        command[position:position]=['--mount',f'type=bind,src={ROOT / "tests"},dst=/checks,readonly',
                                    '--env=PYTHONPATH=/app','--env=PYTEST_DISABLE_PLUGIN_AUTOLOAD=1']
        command += ['/harness/ci_memory_probe.py','python','-m','pytest','-q','-p','no:cacheprovider',
                    '/checks/test_campaign_fault_matrix.py','/checks/test_cellplan_v2_fault_matrix.py',
                    '/checks/test_runtime_deadline.py']
        result=subprocess.run(command,capture_output=True,text=True,timeout=180)
        state=json.loads(subprocess.check_output(['docker','inspect','stai-fault-probe']))[0]
        subprocess.run(['docker','rm','stai-fault-probe'],capture_output=True,check=True)
        # These fixtures are synthetic and contain no credentials or goldens.
        print(result.stdout,end='');print(result.stderr,end='')
        assert result.returncode==0 and not state['State']['OOMKilled'],'resource tests failed'
        telemetry=[json.loads(x.split('=',1)[1]) for x in result.stderr.splitlines() if x.startswith('PUBLIC1_MEMORY_JSON=')][-1]
        assert telemetry['peak_bytes']<=1_000_000_000 and telemetry['swap_limit_bytes']==0


def main():
    calls=[]
    class Remote:
        def post(self,route,body,question,**kwargs):
            assert route=='/chat/0' and question=='probe'
            calls.append(json.loads(body))
            return {'transport_probe':True}
    with tempfile.TemporaryDirectory(prefix='stai-boundary-') as folder:
        work=Path(folder); inputs=work/'input'; inputs.mkdir()
        sockets=work/'sockets'; sockets.mkdir(); sockets.chmod(0o755)
        run={'number':0,'configuration':{'api_enabled':True,'policy':'fallback','output_tokens':600}}
        server=ScopedProxy(sockets/'request.sock',Remote(),run,
                           [{'id':'probe','query':'Synthetic boundary probe','file_name':'probe.xlsx'}])
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.1},daemon=True)
        thread.start()
        try:
            with bounded_output(work/'output'):
                command=docker_base('stai-boundary-probe',IMAGE,inputs,work/'output',sockets)+[
                    '/harness/ci_memory_probe.py','python','/harness/ci_container_boundary_probe.py',
                    '--socket','/gateway/request.sock','--exercise']
                result=subprocess.run(command,capture_output=True,text=True,timeout=60)
                state=json.loads(subprocess.check_output(['docker','inspect','stai-boundary-probe']))[0]
                subprocess.run(['docker','rm','stai-boundary-probe'],capture_output=True,check=True)
                assert result.returncode==0, 'container boundary probe failed'
                assert state['HostConfig']['NetworkMode']=='none'
                assert state['HostConfig']['Memory']==1_000_000_000
                assert state['HostConfig']['MemorySwap']==1_000_000_000
                assert len(calls)==1
                print(result.stdout,end=''); print(result.stderr,end='')
        finally:
            server.shutdown();server.server_close();thread.join()
        resource_checks(work,inputs,sockets)


if __name__=='__main__':
    main()
