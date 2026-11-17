import json
from unittest.mock import patch, MagicMock
import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.core.auth import current_user
from backend.worker.auto_applier import AutoApplierWorker, SubmissionUncertain
from backend.core.model_router import ModelRouter

client=TestClient(app,raise_server_exceptions=False)

def test_api_rejects_unauthenticated_requests():
    with patch('backend.core.auth.get_supabase_client') as db:
        for method,path,body in [('get','/api/dashboard',None),('post','/api/agent/run',{}),('patch','/api/profile/settings',{}),('post','/api/qa/answer',{'answers':[]})]:
            response=getattr(client,method)(path,**({'json':body} if body is not None else {}))
            assert response.status_code==401
        db.assert_not_called()

def test_identity_cannot_be_overridden_and_settings_are_allowlisted():
    app.dependency_overrides[current_user]=lambda:'owner'
    try:
        assert client.post('/api/agent/run',json={'user_id':'victim'}).status_code==422
        assert client.patch('/api/profile/settings',json={'id':'victim'}).status_code==422
        assert client.patch('/api/profile/settings',json={'daily_apply_limit':999}).status_code==422
    finally:
        app.dependency_overrides.clear()

def test_invalid_token_is_rejected():
    with patch('backend.core.auth.get_supabase_client') as get:
        get.return_value.auth.get_user.side_effect=ValueError('invalid')
        assert client.get('/api/dashboard',headers={'Authorization':'Bearer invalid'}).status_code==401

def test_database_failure_is_not_success():
    app.dependency_overrides[current_user]=lambda:'owner'
    try:
        with patch('backend.api.routes.get_supabase_client',side_effect=RuntimeError('private db failure')):
            response=client.get('/api/dashboard')
            assert response.status_code==503
            assert 'private db failure' not in response.text
    finally:
        app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_screenshot_without_confirmation_is_uncertain():
    class Driver:
        async def open_and_extract_schema(self,url):return [{'selector':'#name','type':'text'}]
        async def fill_and_submit(self,**kwargs):return b'PNG only'
        async def close(self):pass
    async def transport(*args,**kwargs):return json.dumps({'mapped_fields':{'#name':'Candidate'}})
    worker=AutoApplierWorker(ModelRouter(custom_transport_handler=transport),driver_factory=Driver)
    result=await worker.process_single_application({'id':'a'},{'id':'u'},[],{'apply_url':'https://example.test'},persist_to_supabase=False)
    assert result.status=='submission_uncertain'
    assert result.proof_screenshot_url is None

@pytest.mark.asyncio
async def test_confirmation_timeout_cannot_report_applied():
    class Driver:
        async def open_and_extract_schema(self,url):return [{'selector':'#name','type':'text'}]
        async def fill_and_submit(self,**kwargs):raise SubmissionUncertain('Timed out after submit')
        async def close(self):pass
    async def transport(*args,**kwargs):return json.dumps({'mapped_fields':{'#name':'Candidate'}})
    result=await AutoApplierWorker(ModelRouter(custom_transport_handler=transport),driver_factory=Driver).process_single_application({'id':'a'},{'id':'u'},[],{'apply_url':'https://example.test'},persist_to_supabase=False)
    assert result.status=='submission_uncertain'

def test_failed_evidence_upload_never_returns_proof():
    with patch('backend.worker.auto_applier.get_supabase_client') as get:
        get.return_value.storage.from_.return_value.upload.side_effect=RuntimeError('storage unavailable')
        with pytest.raises(SubmissionUncertain):
            AutoApplierWorker._upload_proof_and_mark_applied('u','a',b'PNG',{'text':'application received'}, {})
        get.return_value.table.assert_not_called()

def test_dashboard_ownership_comes_from_session():
    db=MagicMock()
    db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data=[]
    db.table.return_value.select.return_value.eq.return_value.execute.return_value.data=[]
    db.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data=[]
    app.dependency_overrides[current_user]=lambda:'authenticated-owner'
    try:
        with patch('backend.api.routes.get_supabase_client',return_value=db):
            assert client.get('/api/dashboard?user_id=victim').status_code==200
        calls=str(db.mock_calls)
        assert 'victim' not in calls
        assert 'authenticated-owner' in calls
    finally:app.dependency_overrides.clear()
