from playwright.sync_api import sync_playwright

def get_jobs(search_url):
    jobs_found = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--single-process", "--no-zygote"]
        ) 
        
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        page.goto(search_url)
        
        print(f"DEBUG - Scraper loaded page title: {page.title()}")
        
        # Smart Wait using a more universal, resilient CSS selector
        try:
            page.wait_for_selector('.base-card, .job-search-card', timeout=10000)
            print("DEBUG - Job cards successfully appeared on the screen!")
        except Exception:
            print("DEBUG - Timeout: The universal job cards never appeared.")
        
        # Grab all the cards using the universal selector
        job_cards = page.locator('.base-card, .job-search-card').all()
        print(f"DEBUG - Found {len(job_cards)} job cards in the HTML.")
        
        for i, card in enumerate(job_cards):
            try:
                # Using looser selectors inside the card as well
                title = card.locator('.base-search-card__title').inner_text().strip()
                company = card.locator('.base-search-card__subtitle').inner_text().strip()
                link = card.locator('a.base-card__full-link').get_attribute('href')
                clean_link = link.split('?')[0] 
                job_id = clean_link.split('-')[-1]
                
                jobs_found.append({"id": job_id, "title": title, "company": company, "link": clean_link})
            except Exception as e:
                print(f"DEBUG - Failed to extract data from card #{i}: {e}")
                continue 
                
        print(f"DEBUG - Successfully extracted and parsed {len(jobs_found)} jobs.")
        browser.close()
        
    return jobs_found