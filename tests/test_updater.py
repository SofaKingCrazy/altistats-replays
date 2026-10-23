import importlib.util
import json
from pathlib import Path

TARGET=Path(__file__).resolve().parents[1]/'scripts'/'update_games.py'
spec=importlib.util.spec_from_file_location('updater',TARGET)
u=importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)

UUID='11111111-2222-4333-8444-555555555555'

def test_fixture_record():
    node={'game_id':UUID,'date':'2026-10-21T20:31:29Z','duration_seconds':540,'map':'ball_4solarbattle','team1':['goal','jose','neil','twig'],'team2':['apathy','crazy','guss','zz'],'score1':4,'score2':6,'series':0}
    r=u.normalize(node)
    assert r and r[0]==UUID and r[6:8]==[4,6]
    assert u.REPLAY_BASE+r[0]+'.pb'=='https://www.altistats.com/viewer/?f='+UUID+'.pb'
    assert u.normalize({**node,'score2':None}) is None
    assert u.normalize({**node,'team1':[]}) is None
    out={};u.scan({'payload':{'games':[node]}},0,out)
    assert UUID in out
    node2={'replayUrl':'https://www.altistats.com/viewer/?f='+UUID+'.pb','startTime':'2026-10-21T20:31:29Z','gameLength':'9m 0s','mapName':'ball_4solarbattle','teams':[{'players':[{'handle':'goal'},{'handle':'jose'}], 'score':4},{'players':['crazy','guss'],'score':6}]}
    assert u.normalize(node2)[2]==540

def test_archived_data():
    r=json.loads((TARGET.parents[1]/'data'/'matches.json').read_text())
    assert len(r)==9190 and len({x[0] for x in r})==9190
    assert all(len(x)==9 for x in r)
    h=(TARGET.parents[1]/'index.html').read_text()
    assert 'Highest score first' in h and 'Lowest score first' in h
    assert 'Magnetic Duck - Altistats.com' in h
    assert 'Van1shing - planeball.com' in h
    assert 'window.ALTISTATS_DATA' in h
