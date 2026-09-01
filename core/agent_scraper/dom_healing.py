"""
DOM Healing module for intelligent selector recovery.
Uses vision AI to analyze screenshots and extract CSS selectors.
"""
import asyncio
import base64
import logging
from typing import Any, Dict, Optional

from playwright.async_api import Page

logger = logging.getLogger("dom_healing")


class DOMHealer:
    """
    Intelligent DOM healing agent that uses vision AI to recover
    CSS selectors when standard scraping fails.
    """

    def __init__(self, ai_service: Any = None):
        self.ai_service = ai_service
        self._selector_cache: Dict[str, str] = {}

    async def _capture_screenshot(self, page: Page) -> str:
        """Capture a base64 encoded screenshot of the current page."""
        try:
            screenshot_bytes = await page.screenshot(type="png")
            return base64.b64encode(screenshot_bytes).decode("utf-8")
        except Exception as e:
            logger.error(f"Failed to capture screenshot: {e}")
            return ""

    async def _extract_html_snippet(self, page: Page) -> str:
        """Extract HTML snippet from the current page."""
        try:
            return await page.content()
        except Exception as e:
            logger.error(f"Failed to extract HTML snippet: {e}")
            return ""

    async def heal_selectors(
        self,
        page: Page,
        selector_name: str,
        screenshot_b64: str,
        html_snippet: str,
        cache_key: str,
    ) -> Dict[str, Any]:
        """
        Use vision AI to analyze the screenshot and extract a working CSS selector.
        
        Args:
            page: Playwright page object
            selector_name: Name of the selector to heal (e.g., "comment_container")
            screenshot_b64: Base64 encoded screenshot
            html_snippet: HTML content of the page
            cache_key: Cache key for storing results
            
        Returns:
            Dict with 'success' boolean and optionally 'selector' or 'error' keys
        """
        # Check cache first
        if cache_key in self._selector_cache:
            return {
                "success": True,
                "selector": self._selector_cache[cache_key],
                "cached": True,
            }

        try:
            # Use AI service to analyze the screenshot
            if self.ai_service:
                prompt = f"""
                Analyze this Instagram page screenshot and HTML snippet.
                The goal is to find a CSS selector that targets the comments container/section.
                
                Look for:
                - The main comments column/div
                - Scrollable areas containing comments
                - Elements with classes like 'comments', 'comment', 'thread', 'list'
                
                Return ONLY a valid CSS selector string (e.g., 'div._a9ym, div._aa03')
                Do not include any explanation or text, just the selector.
                """
                
                # Try to use vision model if available
                if hasattr(self.ai_service, 'analyze_vision'):
                    result = await self.ai_service.analyze_vision(
                        image_b64=screenshot_b64,
                        prompt=prompt,
                        html_context=html_snippet[:4000]  # Limit context size
                    )
                    if result and result.get("selector"):
                        selector = result["selector"]
                        self._selector_cache[cache_key] = selector
                        return {"success": True, "selector": selector}
                
                # Fallback: Try to find common Instagram comment selectors
                common_selectors = [
                    'div._a9ym',
                    'div._aa03',
                    'div[role="main"] div[role="feed"]',
                    'div._a9z6',
                    'div._a9ym div._aa03',
                    'div._a9z6 div._aa03',
                    'section._ae5m',
                    'div._a9ym > div',
                    'div[data-testid="comments-container"]',
                ]
                
                # Test selectors against the page
                for selector in common_selectors:
                    try:
                        count = await page.locator(selector).count()
                        if count > 0:
                            self._selector_cache[cache_key] = selector
                            return {"success": True, "selector": selector}
                    except Exception:
                        continue
            
            return {
                "success": False,
                "error": "No AI service available and no common selectors matched",
            }
            
        except Exception as e:
            logger.error(f"DOM healing failed: {e}")
            return {
                "success": False,
                "error": str(e),
            }
