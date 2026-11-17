"""Authenticated API. Long-running work is queued for the persistent worker."""
from datetime import datetime, timezone
from uuid import uuid4
from typing import Optional
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from backend.core.auth import current_user
from backend.core.database import get_supabase_client
from backend.services.cv_parser import CVParserService

router = APIRouter(prefix='/api')

class SettingsRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    auto_apply_enabled: Optional[bool] = None
    daily_apply_limit: Optional[int] = Field(None, ge=1, le=200)
    min_match_score: Optional[int] = Field(None, ge=0, le=100)
    target_roles: Optional[list[str]] = Field(None, max_length=20)
    target_locations: Optional[list[str]] = Field(None, max_length=20)

class Answer(BaseModel):
    question_key: str = Field(min_length=1, max_length=128)
    answer_value: str = Field(min_length=1, max_length=5000)

class AnswersRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    answers: list[Answer] = Field(max_length=100)

class RunRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    run_discovery: bool = True
    run_auto_apply: bool = False

@router.get('/health')
def health():
    return {'status': 'healthy'}

@router.get('/dashboard')
def dashboard(user_id: str = Depends(current_user)):
    db = get_supabase_client()
    profile = db.table('candidate_profiles').select('*').eq('id',user_id).limit(1).execute().data
    qa = db.table('candidate_qa_bank').select('*').eq('user_id',user_id).execute().data
    apps = db.table('applications').select('*,jobs_discovered(company_name,job_title,location,remote_type,source,apply_url)').eq('user_id',user_id).order('created_at',desc=True).limit(100).execute().data
    for app in apps:
        path = app.get('proof_screenshot_url')
        app['proof_screenshot_url'] = None
        if path and path.startswith(user_id + '/'):
            try:
                app['proof_screenshot_url'] = db.storage.from_('proofs').create_signed_url(path,300)['signedURL']
            except Exception:
                app['evidence_review_required'] = True
    runs = db.table('agent_runs').select('*').eq('user_id',user_id).order('created_at',desc=True).limit(1).execute().data
    heartbeat = db.table('worker_heartbeats').select('heartbeat_at').eq('id','executor').limit(1).execute().data
    worker_online = bool(heartbeat and (datetime.now(timezone.utc)-datetime.fromisoformat(heartbeat[0]['heartbeat_at'].replace('Z','+00:00'))).total_seconds()<90)
    return {'worker_online':worker_online,'profile': profile[0] if profile else None,'qa_bank':qa,'applications':apps,'run':runs[0] if runs else None}

@router.post('/cv/upload')
async def upload(file: UploadFile = File(...), user_id: str = Depends(current_user)):
    data = await file.read(10*1024*1024+1)
    if len(data)>10*1024*1024:
        raise HTTPException(413,'PDF must be 10 MB or smaller.')
    if not data.startswith(b'%PDF-'):
        raise HTTPException(422,'Upload a valid PDF resume.')
    path = f'{user_id}/{uuid4()}.pdf'
    db = get_supabase_client()
    db.storage.from_('resumes').upload(path,data,{'content-type':'application/pdf'})
    try:
        await CVParserService().parse_and_ingest_cv(user_id,data,resume_storage_path=path)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc
    state = dashboard(user_id)
    return {**(state['profile'] or {}),'user_id':user_id,'gap_questions':state['qa_bank']}

@router.patch('/profile/settings')
def settings(payload: SettingsRequest, user_id: str = Depends(current_user)):
    db = get_supabase_client()
    rows = db.table('candidate_profiles').select('parsed_cv_json').eq('id',user_id).limit(1).execute().data
    if not rows:
        raise HTTPException(404,'Upload your CV first.')
    update = payload.model_dump(exclude_none=True)
    cv = dict(rows[0].get('parsed_cv_json') or {})
    for key in ('target_roles','target_locations'):
        if key in update:
            cv[key] = [x.strip() for x in update.pop(key) if x.strip()]
    update['parsed_cv_json'] = cv
    update['embedding_version'] = None
    db.table('candidate_profiles').update(update).eq('id',user_id).execute()
    return {'status':'ok'}

@router.post('/qa/answer')
def answers(payload: AnswersRequest,user_id: str = Depends(current_user)):
    db = get_supabase_client()
    for answer in payload.answers:
        if not answer.answer_value.strip():
            raise HTTPException(422,'Answers cannot be blank.')
        rows = db.table('candidate_qa_bank').update({'answer_value':answer.answer_value.strip(),'is_answered':True}).eq('user_id',user_id).eq('question_key',answer.question_key).execute().data
        if not rows:
            raise HTTPException(404,'Question not found.')
    qa = db.table('candidate_qa_bank').select('question_key,is_answered').eq('user_id',user_id).execute().data
    answered = {q['question_key'] for q in qa if q['is_answered']}
    paused = db.table('applications').select('id,required_question_keys').eq('user_id',user_id).eq('status','needs_human_input').execute().data
    for app in paused:
        required = app.get('required_question_keys') or []
        if required and set(required).issubset(answered):
            db.table('applications').update({'status':'queued','error_log':None}).eq('id',app['id']).eq('status','needs_human_input').execute()
    return {'status':'ok'}

@router.post('/agent/run',status_code=202)
def enqueue(payload: RunRequest,user_id: str = Depends(current_user)):
    db = get_supabase_client()
    profile = db.table('candidate_profiles').select('id').eq('id',user_id).limit(1).execute().data
    if not profile:
        raise HTTPException(404,'Upload your CV first.')
    existing = db.table('agent_runs').select('*').eq('user_id',user_id).in_('status',['queued','running']).execute().data
    if existing:
        return {'run':existing[0]}
    try:
        run = db.table('agent_runs').insert({'user_id':user_id,**payload.model_dump()}).execute().data[0]
    except Exception:
        existing = db.table('agent_runs').select('*').eq('user_id',user_id).in_('status',['queued','running']).execute().data
        if existing:
            return {'run':existing[0]}
        raise
    return {'run':run}
