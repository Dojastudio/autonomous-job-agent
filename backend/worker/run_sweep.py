"""Persistent worker. This process is the only scheduler and application executor."""
import asyncio
import logging
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from backend.core.database import get_supabase_client
from backend.core.config import settings
from backend.services.job_ingestor import JobIngestorService
from backend.scrapers.job_feeds import JobFeedScraper
from backend.worker.auto_applier import AutoApplierWorker

log = logging.getLogger(__name__)

def now():
    return datetime.now(timezone.utc).isoformat()

async def execute(run, token):
    db = get_supabase_client()
    def progress(message):
        db.table('agent_runs').update({'progress':message,'heartbeat_at':now()}).eq('id',run['id']).eq('worker_token',token).eq('status','running').execute()
    profile = db.table('candidate_profiles').select('*').eq('id',run['user_id']).single().execute().data
    summary = {}
    if run['run_discovery']:
        progress('Discovering and scoring jobs')
        jobs = await JobFeedScraper().fetch_all_feeds()
        existing = db.table('applications').select('job_id').eq('user_id',run['user_id']).execute().data
        result = await JobIngestorService().score_and_queue_for_candidate(profile,jobs,{a['job_id'] for a in existing})
        summary = {'jobs_fetched':result.jobs_fetched,'newly_queued_count':len(result.applications_queued)}
    if run['run_auto_apply']:
        if not settings.WORKER_AUTO_APPLY_ENABLED:
            raise RuntimeError('Employer submissions are disabled for this deployment.')
        progress('Processing queued applications')
        if not profile.get('resume_url'):
            raise ValueError('Upload a resume before applying.')
        resume = db.storage.from_('resumes').download(profile['resume_url'])
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory)/'resume.pdf'
            pdf.write_bytes(resume)
            while True:
                claimed = db.rpc('claim_application',{'p_run':run['id'],'p_token':token}).execute().data
                if not claimed:
                    break
                app = claimed[0]
                job = db.table('jobs_discovered').select('*').eq('id',app['job_id']).single().execute().data
                qa = db.table('candidate_qa_bank').select('*').eq('user_id',run['user_id']).execute().data
                outcome = await AutoApplierWorker().process_single_application(app,profile,qa,job,str(pdf))
                db.table('application_attempts').update({'status':outcome.status,'error':outcome.error_log,'confirmation':outcome.confirmation,'finished_at':now()}).eq('run_id',run['id']).eq('application_id',app['id']).eq('status','applying').execute()
                progress('Processing queued applications')
    db.table('agent_runs').update({'status':'completed','progress':'Completed','result':summary,'finished_at':now()}).eq('id',run['id']).eq('worker_token',token).eq('status','running').execute()

async def heartbeat(run_id,token):
    while True:
        await asyncio.sleep(20)
        await asyncio.to_thread(lambda: get_supabase_client().table('worker_heartbeats').upsert({'id':'executor','heartbeat_at':now()}).execute())
        rows = await asyncio.to_thread(lambda: get_supabase_client().table('agent_runs').update({'heartbeat_at':now()}).eq('id',run_id).eq('worker_token',token).eq('status','running').execute().data)
        if not rows:
            raise RuntimeError('Worker lost ownership of this run')

def schedule_due():
    db = get_supabase_client()
    for profile in db.table('candidate_profiles').select('id').eq('auto_apply_enabled',True).execute().data:
        recent = db.table('agent_runs').select('id').eq('user_id',profile['id']).gte('created_at',(datetime.now(timezone.utc)-timedelta(hours=settings.DISCOVERY_INTERVAL_HOURS)).isoformat()).limit(1).execute().data
        if not recent:
            try:
                db.table('agent_runs').insert({'user_id':profile['id'],'run_discovery':True,'run_auto_apply':settings.WORKER_AUTO_APPLY_ENABLED}).execute()
            except Exception as exc:
                if '23505' not in str(exc):
                    raise

async def main():
    while True:
        try:
            db = get_supabase_client()
            db.table('worker_heartbeats').upsert({'id':'executor','heartbeat_at':now()}).execute()
            db.rpc('recover_stale_runs').execute()
            if settings.WORKER_SCHEDULE_ENABLED:
                schedule_due()
            token = str(uuid4())
            claimed = db.rpc('claim_agent_run',{'p_token':token}).execute().data
            if not claimed:
                await asyncio.sleep(5)
                continue
            run = claimed[0]
            beat = asyncio.create_task(heartbeat(run['id'],token))
            work = asyncio.create_task(execute(run,token))
            try:
                done,_ = await asyncio.wait([beat,work],return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            except Exception:
                work.cancel()
                await asyncio.gather(work,return_exceptions=True)
                log.exception('Run failed %s',run['id'])
                # Any interrupted in-flight attempt is uncertain, never automatically queued again.
                attempts = db.table('application_attempts').select('application_id').eq('run_id',run['id']).eq('status','applying').execute().data
                for attempt in attempts:
                    db.table('applications').update({'status':'submission_uncertain','error_log':'Execution interrupted; review before retrying.'}).eq('id',attempt['application_id']).eq('status','applying').execute()
                db.table('application_attempts').update({'status':'submission_uncertain','finished_at':now()}).eq('run_id',run['id']).eq('status','applying').execute()
                db.table('agent_runs').update({'status':'failed','error':'Processing failed. Review worker logs and application states.','finished_at':now()}).eq('id',run['id']).eq('worker_token',token).execute()
            finally:
                work.cancel(); beat.cancel()
                await asyncio.gather(work,beat,return_exceptions=True)
        except Exception:
            log.exception('Worker loop failed')
            await asyncio.sleep(10)

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
