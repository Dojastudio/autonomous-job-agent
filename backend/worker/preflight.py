"""Read-only provider/model availability check. Does not send candidate information."""
import asyncio
import httpx
from backend.core.model_router import TASK_ROUTES
from backend.core.config import settings

async def main():
    seen=set(); available=0
    async with httpx.AsyncClient(timeout=20) as client:
        for route in TASK_ROUTES.values():
            for endpoint in route:
                identity=(endpoint.provider,endpoint.model)
                if identity in seen:continue
                seen.add(identity)
                key=getattr(settings,endpoint.api_key_attr,'')
                if not key:
                    print(f'{endpoint.provider}: not configured');continue
                try:
                    response=await client.get(endpoint.base_url.rstrip('/')+'/models',headers={'Authorization':f'Bearer {key}'})
                    response.raise_for_status()
                    ids={model['id'].removeprefix('models/') for model in response.json().get('data',[])}
                    supported=endpoint.model in ids
                    available+=supported
                    print(f'{endpoint.provider}/{endpoint.model}: '+('available' if supported else 'not listed'))
                except Exception:
                    print(f'{endpoint.provider}: could not verify access')
    if not available:
        raise SystemExit('No configured model was verified.')

if __name__=='__main__':asyncio.run(main())
