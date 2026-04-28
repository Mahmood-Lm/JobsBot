from playwright.sync_api import sync_playwright

def get_jobs(search_url):
    jobs_found = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--single-process", "--no-zygote"]
        ) 
        
        # 1. Add a real User-Agent so LinkedIn thinks this is a normal human
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        page.goto(search_url)
        
        # 2. Log the Page Title to CloudWatch so we can "see" what the bot sees
        print(f"DEBUG - Scraper loaded page title: {page.title()}")
        
        # 3. Smart Wait: Give it up to 10 seconds to resolve the location redirect
        try:
            page.wait_for_selector('ul.jobs-search__results-list > li', timeout=10000)
            print("DEBUG - Job list successfully loaded!")
        except Exception:
            print("DEBUG - Timeout: Could not find the job list. We might be blocked.")
        
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