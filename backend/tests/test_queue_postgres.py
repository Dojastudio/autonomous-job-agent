"""Run only against an isolated fixture database via JOB_AID_TEST_DATABASE_URL."""
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest

URL=os.getenv('JOB_AID_TEST_DATABASE_URL')
pytestmark=pytest.mark.skipif(not URL,reason='Isolated PostgreSQL fixture not configured')

def sql(query):
    return subprocess.check_output(['psql',URL,'-X','-q','-t','-A','-v','ON_ERROR_STOP=1','-c',query],text=True).strip()

def test_concurrent_claims_quota_fencing_and_recovery():
    user,token,wrong=str(uuid4()),str(uuid4()),str(uuid4())
    sql(f"INSERT INTO candidate_profiles(id,auto_apply_enabled,daily_apply_limit) VALUES('{user}',true,2)")
    run=sql(f"INSERT INTO agent_runs(user_id) VALUES('{user}') RETURNING id")
    for _ in range(4):sql(f"INSERT INTO applications(user_id) VALUES('{user}')")
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims=list(pool.map(lambda _:sql(f"SELECT id FROM claim_agent_run('{token}')"),range(2)))
    assert [x for x in claims if x]==[run]
    assert sql(f"SELECT id FROM claim_application('{run}','{wrong}')")==''
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims=list(pool.map(lambda _:sql(f"SELECT id FROM claim_application('{run}','{token}')"),range(4)))
    claimed=[x for x in claims if x]
    assert len(set(claimed))==len(claimed)==2
    assert sql(f"SELECT count(*) FROM application_attempts WHERE user_id='{user}'")=='2'
    sql(f"UPDATE agent_runs SET heartbeat_at=now()-interval '11 minutes' WHERE id='{run}'")
    assert sql('SELECT recover_stale_runs()')=='1'
    assert sql(f"SELECT count(*) FROM applications WHERE user_id='{user}' AND status='submission_uncertain'")=='2'
    assert sql(f"SELECT id FROM claim_application('{run}','{token}')")==''

def test_disabled_automation_never_claims():
    user,token=str(uuid4()),str(uuid4())
    sql(f"INSERT INTO candidate_profiles(id) VALUES('{user}')")
    run=sql(f"INSERT INTO agent_runs(user_id) VALUES('{user}') RETURNING id")
    sql(f"INSERT INTO applications(user_id) VALUES('{user}')")
    sql(f"SELECT id FROM claim_agent_run('{token}')")
    assert sql(f"SELECT id FROM claim_application('{run}','{token}')")==''

def test_runs_are_owner_scoped_and_claims_are_worker_only():
    owner,other=str(uuid4()),str(uuid4())
    for uid in (owner,other):
        sql(f"INSERT INTO candidate_profiles(id) VALUES('{uid}')")
        sql(f"INSERT INTO agent_runs(user_id,status) VALUES('{uid}','completed')")
    visible=sql(f"SET ROLE authenticated; SELECT set_config('request.jwt.claim.sub','{owner}',false); SELECT user_id FROM agent_runs")
    assert owner in visible
    assert other not in visible
    assert sql("SELECT has_function_privilege('authenticated','claim_agent_run(uuid)','EXECUTE')")=='f'
    assert sql("SELECT has_function_privilege('anon','recover_stale_runs()','EXECUTE')")=='f'
