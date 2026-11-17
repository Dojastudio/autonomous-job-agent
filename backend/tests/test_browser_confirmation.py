"""Controlled local forms: no applications or external network requests."""
import os
import pytest
from backend.worker.auto_applier import PlaywrightStealthDriver, DOM_FORM_EXTRACTOR_JS, SubmissionUncertain

pytestmark=pytest.mark.skipif(os.getenv('JOB_AID_BROWSER_TESTS')!='1',reason='Browser integration opt-in')

@pytest.mark.asyncio
async def test_real_browser_confirmation_and_validation():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser=await p.chromium.launch(channel='chrome',headless=True)
        page=await browser.new_page()
        driver=PlaywrightStealthDriver()
        driver._page=page
        try:
            await page.set_content('''<form onsubmit="event.preventDefault();document.body.innerHTML='<h1>Application received</h1>'"><input id="name" required><button type="submit">Submit</button></form>''')
            schema=await page.evaluate(DOM_FORM_EXTRACTOR_JS)
            result=await driver.fill_and_submit(schema,{'#name':'Test Candidate'})
            assert result.confirmation['text']=='application received'
            assert result.screenshot.startswith(b'\x89PNG')
            await page.set_content('<form><input id="name" required><button type="submit">Submit</button></form>')
            schema=await page.evaluate(DOM_FORM_EXTRACTOR_JS)
            with pytest.raises(SubmissionUncertain):
                await driver.fill_and_submit(schema,{})
            await page.set_content('<form onsubmit="event.preventDefault();document.body.innerHTML=\'<h1>No application received</h1>\'"><button type="submit">Submit</button></form>')
            with pytest.raises(SubmissionUncertain):
                await driver.fill_and_submit([],{})
            await page.set_content('<h1>Application received</h1><form><button type="submit">Submit</button></form>')
            with pytest.raises(SubmissionUncertain):
                await driver.fill_and_submit([],{})
        finally:
            await browser.close()
