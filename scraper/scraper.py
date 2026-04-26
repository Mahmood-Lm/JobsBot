from playwright.sync_api import sync_playwright
import time

def get_jobs(search_url):
    jobs_found = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--single-process", "--no-zygote"]
        ) 
        page = browser.new_page()
        page.goto(search_url)
        time.sleep(3) 
        
        job_cards = page.locator('ul.jobs-search__results-list > li').all()
        for card in job_cards:
            try:
                title = card.locator('h3.base-search-card__title').inner_text().strip()
                company = card.locator('h4.base-search-card__subtitle').inner_text().strip()
                link = card.locator('a.base-card__full-link').get_attribute('href')
                clean_link = link.split('?')[0] 
                job_id = clean_link.split('-')[-1]
                jobs_found.append({"id": job_id, "title": title, "company": company, "link": clean_link})
            except Exception:
                continue 
        browser.close()
    return jobs_found