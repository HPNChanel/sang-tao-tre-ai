"""Observable kernel/Unix-proxy checks inside a network-none container."""
import argparse
import json
from pathlib import Path
import signal
import socket

from eval_unix_solver import UnixHTTP


def request(path, route, body):
    connection = UnixHTTP(path, 4)
    try:
        connection.request("POST",route,body=body,headers={"Content-Type":"application/json"})
        response=connection.getresponse()
        return response.status,json.loads(response.read(2_000_000))
    finally:
        connection.close()


def verify(path, exercise=False):
    interfaces=sorted(p.name for p in Path('/sys/class/net').iterdir())
    assert interfaces==['lo'], "unexpected network interface"
    tcp_blocked=False
    try:
        with socket.create_connection(('203.0.113.1',443),timeout=.5):
            pass
    except OSError:
        tcp_blocked=True
    assert tcp_blocked
    def expired(*_):
        raise TimeoutError('DNS bounded probe')
    previous=signal.signal(signal.SIGALRM,expired)
    signal.setitimer(signal.ITIMER_REAL,1)
    try:
        try:
            socket.getaddrinfo('stai-network-probe.invalid',443)
            dns_blocked=False
        except OSError:
            dns_blocked=True
    finally:
        signal.setitimer(signal.ITIMER_REAL,0); signal.signal(signal.SIGALRM,previous)
    assert dns_blocked
    assert not Path('/var/run/docker.sock').exists()
    assert request(path,'/outside',b'{}')[0]==403
    status,data=request(path,'/probe',b'{}')
    assert status==200 and data=={'scoped_proxy':True}
    if exercise:
        assert request(path,'/begin',b'{"id":"probe"}')[0]==200
        body={'model':'qwen/qwen3.7-flash','max_tokens':600,'temperature':0,
              'response_format':{'type':'json_object'},'reasoning':{'enabled':False},
              'messages':[{'role':'system','content':'JSON only'},
                          {'role':'user','content':'PLAN SCHEMA:\n{}\n\nQUESTION:\nSynthetic boundary probe\n'}]}
        status,data=request(path,'/chat',json.dumps(body).encode())
        assert status==200 and data.get('transport_probe') is True
        assert request(path,'/end',b'{}')[0]==200
    return {'pass':True,'interfaces':interfaces,'native_tcp_blocked':tcp_blocked,
            'native_dns_blocked':dns_blocked,'other_route_blocked':True,
            'scoped_unix_proxy_reachable':True,'synthetic_planning_call':exercise}


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--socket',required=True)
    parser.add_argument('--exercise',action='store_true'); args=parser.parse_args()
    print('BOUNDARY_JSON='+json.dumps(verify(args.socket,args.exercise)),flush=True)
