import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from shared.logging import get_logger

logger = get_logger('scraper')

def get_jobs(context, search_url):
    """Opens a tab to scrape the initial job search results."""
    jobs_found = []
    page = context.new_page() # Open a new tab
    
    try:
        page.goto(search_url)
        # print(f"DEBUG - Scraper loaded page title: {page.title()}")
        
        try:
            page.wait_for_selector('.base-card, .job-search-card', timeout=10000)
        except Exception as e:
            logger.error("Timeout waiting for job cards", extra={"error": str(e), "search_url": search_url})
        
        job_cards = page.locator('.base-card, .job-search-card').all()
        for i, card in enumerate(job_cards):
            try:
                title = card.locator('.base-search-card__title').inner_text().strip()
                company = card.locator('.base-search-card__subtitle').inner_text().strip()
                link = card.locator('a.base-card__full-link').get_attribute('href')
                clean_link = link.split('?')[0] 
                job_id = clean_link.split('-')[-1]
                
                job_obj = {"id": job_id, "title": title, "company": company, "link": clean_link}
                jobs_found.append(job_obj)
                
            except Exception as e:
                logger.error("Error processing job card", extra={"error": str(e), "card_index": i})
                continue
    finally:
        page.close() # CRITICAL: Close the tab to free up memory!
        
    return jobs_found


def get_job_description(context, job_url):
    """Opens a tab to extract the full description text of a specific job."""
    page = context.new_page() # Open a new tab
    
    # Block heavy media from loading
    page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "media", "stylesheet", "font", "other"] else route.continue_())
    
    try:
        page.goto(job_url, wait_until="domcontentloaded", timeout=15000)
        selectors = '.description__text, .show-more-less-html__markup, .core-section-container__content, .jobs-description-content__text'
        page.wait_for_selector(selectors, timeout=8000)
        description = page.locator(selectors).first.inner_text()
        logger.info("Successfully scraped job description", extra={"job_url": job_url[:50]})
    except Exception as e:
        logger.error("Could not load full description", extra={"error": str(e), "job_url": job_url[:50]})
        description = "Description not available."
    finally:
        page.close() # CRITICAL: Close the tab!
        
    return description