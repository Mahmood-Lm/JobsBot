import json
import os
import sys
import time

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from shared.logging import get_logger

logger = get_logger('scraper')

def get_jobs(context, search_url):
    """Opens a tab to scrape the initial job search results."""
    jobs_found = []
    page = context.new_page() # Open a new tab
    page.set_default_timeout(5000)

    def _safe_text(locator, timeout_ms=2000):
        try:
            value = locator.first.text_content(timeout=timeout_ms)
            return value.strip() if value else None
        except PlaywrightTimeoutError:
            return None

    def _safe_attr(locator, attr, timeout_ms=2000):
        try:
            return locator.first.get_attribute(attr, timeout=timeout_ms)
        except PlaywrightTimeoutError:
            return None
    
    try:
        page.goto(search_url)
        # print(f"DEBUG - Scraper loaded page title: {page.title()}")
        
        try:
            page.wait_for_selector('.base-card, .job-search-card', timeout=10000)
        except Exception as e:
            logger.error("Timeout waiting for job cards", extra={"error": str(e), "search_url": search_url})

        # Nudge lazy-loaded cards to render before we enumerate them.
        try:
            page.evaluate("""
                () => {
                    const top = 0;
                    const bottom = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
                    window.scrollTo(0, bottom);
                    window.scrollTo(0, top);
                }
            """)
            page.wait_for_timeout(300)
        except Exception as e:
            logger.warning("Lazy-load scroll pass failed", extra={"error": str(e), "search_url": search_url})
        
        cards = page.locator('.base-card, .job-search-card')
        card_count = cards.count()
        for i in range(card_count):
            try:
                title = None
                company = None
                link = None

                for attempt in range(2):
                    card = cards.nth(i)
                    card.scroll_into_view_if_needed(timeout=2000)

                    title = _safe_text(card.locator('.base-search-card__title'))
                    company = _safe_text(card.locator('.base-search-card__subtitle'))
                    link = _safe_attr(card.locator('a.base-card__full-link'), 'href')

                    if title and company and link:
                        break

                    if attempt == 0:
                        page.wait_for_timeout(250)

                if not link:
                    logger.warning("Missing job link", extra={"card_index": i})
                    continue
                if not title or not company:
                    logger.warning("Missing job title or company", extra={"card_index": i})
                    continue

                clean_link = link.split('?')[0] 
                job_id = clean_link.split('-')[-1]
                
                job_obj = {"id": job_id, "title": title, "company": company, "link": clean_link}
                jobs_found.append(job_obj)
                
            except PlaywrightTimeoutError as e:
                logger.warning("Timeout processing job card", extra={"error": str(e), "card_index": i})
                continue
            except Exception as e:
                logger.error("Error processing job card", extra={"error": str(e), "card_index": i})
                continue
    finally:
        page.close() # CRITICAL: Close the tab to free up memory!
        
    return jobs_found


def get_job_description(context, job_url, timeout_ms=15000):
    """Opens a tab to extract the full description text of a specific job."""
    page = context.new_page() # Open a new tab
    
    # Block heavy media from loading
    page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "media", "stylesheet", "font", "other"] else route.continue_())
    
    start_time = time.monotonic()
    try:
        page.goto(job_url, wait_until="domcontentloaded", timeout=timeout_ms)
        selectors = '.description__text, .show-more-less-html__markup, .core-section-container__content, .jobs-description-content__text'
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        remaining_ms = max(500, timeout_ms - elapsed_ms)
        page.wait_for_selector(selectors, timeout=remaining_ms)
        description = page.locator(selectors).first.inner_text()
        logger.info("Successfully scraped job description", extra={"job_url": job_url[:50]})
        return description, False
    except PlaywrightTimeoutError:
        return "Description not available.", True
    except Exception as e:
        logger.error("Could not load full description", extra={"error": str(e), "job_url": job_url[:50]})
        return "Description not available.", False
    finally:
        page.close() # CRITICAL: Close the tab!
        