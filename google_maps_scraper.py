# -*- coding: utf-8 -*-
import time
import csv
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException, ElementClickInterceptedException
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
import re
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
import sys
import io
import string
import importlib
import os
from datetime import datetime, timedelta

# Force UTF-8 encoding for stdout
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Text preprocessing imports
try:
    from textblob import TextBlob
    TEXTBLOB_AVAILABLE = True
except ImportError:
    TEXTBLOB_AVAILABLE = False
    print("Note: textblob not available. Install with: pip install textblob")

try:
    from camel_tools.utils.normalize import normalize_alef_maksura_ar, normalize_alef_ar, normalize_teh_marbuta_ar
    from camel_tools.dialectid import DialectIdentifier
    from camel_tools.utils.dediac import dediac_ar
    CAMEL_AVAILABLE = True
    print("✓ CAMeL Tools loaded successfully")
except ImportError:
    CAMEL_AVAILABLE = False
    print("Note: CAMeL Tools not available. Install with: pip install camel-tools")

try:
    import langdetect
    LANGDETECT_AVAILABLE = True
except ImportError:
    LANGDETECT_AVAILABLE = False
    print("Note: langdetect not available. Install with: pip install langdetect")

try:
    PYPERCLIP_MODULE = importlib.import_module("pyperclip")
    PYPERCLIP_AVAILABLE = True
except Exception:
    PYPERCLIP_MODULE = None
    PYPERCLIP_AVAILABLE = False


def review_has_meaningful_text(text):
    """True if the review has non-empty body text (not rating-only)."""
    if text is None:
        return False
    s = str(text).strip()
    if not s or s.upper() == "N/A":
        return False
    return True


def parse_google_maps_review_date(date_str):
    """
    Parse Google Maps relative or absolute review date strings to a datetime.
    Returns None if unparseable. Aligns with GUI parse_date logic.
    """
    if not date_str or date_str == "N/A":
        return None

    try:
        original_date_str = date_str
        date_str = date_str.strip().lower()

        if "ago" in date_str:
            now = datetime.now()

            if "minute" in date_str:
                minutes = re.findall(r"(\d+)\s*minute", date_str)
                if minutes:
                    return now - timedelta(minutes=int(minutes[0]))

            elif "hour" in date_str:
                hours = re.findall(r"(\d+)\s*hour", date_str)
                if hours:
                    return now - timedelta(hours=int(hours[0]))

            elif "day" in date_str and "week" not in date_str:
                days = re.findall(r"(\d+)\s*day", date_str)
                if days:
                    return now - timedelta(days=int(days[0]))
                if "a day ago" in date_str:
                    return now - timedelta(days=1)

            elif "week" in date_str:
                weeks = re.findall(r"(\d+)\s*week", date_str)
                if weeks:
                    return now - timedelta(weeks=int(weeks[0]))
                if "a week ago" in date_str:
                    return now - timedelta(weeks=1)

            elif "month" in date_str:
                months = re.findall(r"(\d+)\s*month", date_str)
                if months:
                    return now - timedelta(days=int(months[0]) * 30)
                if "a month ago" in date_str:
                    return now - timedelta(days=30)

        date_formats = [
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%m/%d/%Y",
            "%d-%m-%Y",
            "%B %d, %Y",
            "%b %d, %Y",
        ]

        for fmt in date_formats:
            try:
                return datetime.strptime(original_date_str.strip(), fmt)
            except ValueError:
                continue

        return None

    except Exception as e:
        print(f"DEBUG: Error parsing date '{date_str}': {e}")
        return None


class ReviewTextProcessor:
    def __init__(self):
        self.setup_camel_tools()

    def setup_camel_tools(self):
        """Initialize CAMeL Tools components"""
        if CAMEL_AVAILABLE:
            try:
                # Initialize dialect identifier
                self.dialect_id = DialectIdentifier.pretrained()
                print("✓ CAMeL Tools dialect identifier loaded")
            except Exception as e:
                print(f"Warning: Could not load CAMeL Tools dialect identifier: {e}")
                self.dialect_id = None
        else:
            self.dialect_id = None

    def detect_language(self, text):
        """Detect language of text"""
        if not text or len(text.strip()) < 3:
            return 'unknown'

        try:
            if LANGDETECT_AVAILABLE:
                return langdetect.detect(text)
            else:
                # Simple heuristic
                arabic_chars = len(re.findall(r'[\u0600-\u06FF]', text))
                english_chars = len(re.findall(r'[a-zA-Z]', text))

                if arabic_chars > english_chars:
                    return 'ar'
                elif english_chars > arabic_chars:
                    return 'en'
                else:
                    return 'mixed'
        except:
            return 'unknown'

    def normalize_arabic_text(self, text):
        """Normalize Arabic text using CAMeL Tools"""
        if not CAMEL_AVAILABLE or not text:
            return text

        try:
            # Remove diacritics
            text = dediac_ar(text)

            # Normalize different forms of Alef
            text = normalize_alef_ar(text)

            # Normalize Alef Maksura
            text = normalize_alef_maksura_ar(text)

            # Normalize Teh Marbuta
            text = normalize_teh_marbuta_ar(text)

            return text.strip()

        except Exception as e:
            print(f"Warning: Arabic normalization failed: {e}")
            return text

    def identify_arabic_dialect(self, text):
        """Identify Arabic dialect using CAMeL Tools"""
        if not CAMEL_AVAILABLE or not self.dialect_id or not text:
            return 'unknown'

        try:
            predictions = self.dialect_id.predict([text])
            return predictions[0].top if predictions else 'unknown'
        except Exception as e:
            print(f"Warning: Dialect identification failed: {e}")
            return 'unknown'

    def correct_english_text(self, text):
        """Correct English text with confidence checking"""
        if not TEXTBLOB_AVAILABLE or not text:
            return text

        try:
            blob = TextBlob(text)
            corrected = str(blob.correct())

            # Only apply correction if the change makes sense
            original_words = text.lower().split()
            corrected_words = corrected.lower().split()

            # Check if too many words changed (likely false positives)
            if len(original_words) != len(corrected_words):
                return text  # Keep original if word count changed

            changes = sum(1 for o, c in zip(original_words, corrected_words) if o != c)
            change_ratio = changes / len(original_words) if original_words else 0

            # If more than 30% of words changed, probably wrong
            if change_ratio > 0.3:
                return text  # Keep original

            # Check for common false positives
            false_positives = {
                'nice': 'vice',
                'malls': 'walls',
                'mall': 'wall',
                'brands': 'bands',
                'halal': 'hall',
                'options': 'option',
                'if': 'of',
                'upscale': 'scale'
            }

            for original, wrong_correction in false_positives.items():
                if original in text.lower() and wrong_correction in corrected.lower():
                    return text  # Keep original to avoid false positive

            return corrected

        except Exception as e:
            print(f"Warning: English correction failed: {e}")
            return text

    def process_mixed_text(self, text):
        """Process mixed Arabic-English text"""
        if not text:
            return text

        try:
            # Split text into words
            words = text.split()
            processed_words = []

            for word in words:
                # Check if word is primarily Arabic
                arabic_chars = len(re.findall(r'[\u0600-\u06FF]', word))
                english_chars = len(re.findall(r'[a-zA-Z]', word))

                if arabic_chars > 0 and english_chars == 0:
                    # Pure Arabic word - normalize
                    processed_word = self.normalize_arabic_text(word)
                elif english_chars > 0 and arabic_chars == 0:
                    # Pure English word - keep as is (spell correction handled separately)
                    processed_word = word
                else:
                    # Mixed or other - keep as is
                    processed_word = word

                processed_words.append(processed_word)

            return ' '.join(processed_words)

        except Exception as e:
            print(f"Warning: Mixed text processing failed: {e}")
            return text

    def clean_reviewer_name(self, name):
        """Clean reviewer name (Arabic/English/Mixed)"""
        if not name or name == 'N/A':
            return name

        try:
            # Remove extra whitespace
            name = re.sub(r'\s+', ' ', name).strip()

            # Capitalize properly for English parts
            words = name.split()
            cleaned_words = []

            for word in words:
                # Check if word contains Arabic characters
                if re.search(r'[\u0600-\u06FF]', word):
                    # Arabic word - just normalize
                    cleaned_word = self.normalize_arabic_text(word)
                else:
                    # English word - capitalize properly
                    cleaned_word = word.capitalize()

                cleaned_words.append(cleaned_word)

            return ' '.join(cleaned_words)

        except Exception as e:
            print(f"Warning: Name cleaning failed: {e}")
            return name

    def process_arabic_text(self, text):
        """Process Arabic text specifically"""
        if not text:
            return text

        # Normalize using CAMeL Tools
        processed = self.normalize_arabic_text(text)

        # Identify dialect for debugging
        if CAMEL_AVAILABLE and self.dialect_id:
            dialect = self.identify_arabic_dialect(text)
            if dialect != 'unknown':
                print(f"  Detected dialect: {dialect}")

        return processed

    def process_review_text(self, text):
        """Main function to process review text"""
        if not text or text == 'N/A':
            return text

        try:
            # Detect language
            lang = self.detect_language(text)

            if lang == 'ar':
                # Pure Arabic - normalize using CAMeL Tools
                processed_text = self.process_arabic_text(text)

            elif lang == 'en':
                # Pure English - spell correct
                processed_text = self.correct_english_text(text)

            else:
                # Mixed or unknown - process both parts
                processed_text = self.process_mixed_text(text)

                # Try to spell correct English parts
                if TEXTBLOB_AVAILABLE:
                    # Extract English words and correct them
                    english_parts = re.findall(r'[a-zA-Z\s]+', processed_text)
                    for eng_part in english_parts:
                        if len(eng_part.strip()) > 2:
                            corrected = self.correct_english_text(eng_part)
                            processed_text = processed_text.replace(eng_part, corrected)

            # Final cleanup
            processed_text = re.sub(r'\s+', ' ', processed_text).strip()
            return processed_text

        except Exception as e:
            print(f"Warning: Text processing failed: {e}")
            return text

    def preprocess_text(self, text):
        """Unified text preprocessing method - calls process_review_text"""
        return self.process_review_text(text)

    def preprocess_reviews(self, reviews):
        """Preprocess all reviews"""
        if not reviews:
            return reviews

        print("\n" + "="*50)
        print("PREPROCESSING REVIEWS WITH CAMEL TOOLS")
        print("="*50)

        processed_reviews = []

        for i, review in enumerate(reviews, 1):
            print(f"\nProcessing review {i}/{len(reviews)}...")

            processed_review = review.copy()

            # Process reviewer name
            original_name = review['name']
            processed_name = self.clean_reviewer_name(original_name)
            if original_name != processed_name:
                print(f"Name: {original_name} → {processed_name}")
            processed_review['name'] = processed_name

            # Process review text
            original_text = review['text']
            if original_text and original_text != 'N/A':
                processed_text = self.process_review_text(original_text)
                if original_text != processed_text:
                    # Show truncated version for display
                    orig_display = original_text[:50] + "..." if len(original_text) > 50 else original_text
                    proc_display = processed_text[:50] + "..." if len(processed_text) > 50 else processed_text
                    print(f"Text: {orig_display} → {proc_display}")
                processed_review['text'] = processed_text

            processed_reviews.append(processed_review)

        print(f"\n✓ Successfully preprocessed {len(processed_reviews)} reviews!")
        return processed_reviews

    def test_preprocessing(self):
        """Test preprocessing with sample texts"""
        print("\n" + "="*50)
        print("TESTING PREPROCESSING FUNCTIONALITY")
        print("="*50)

        test_cases = [
            # Arabic with slang
            ("المول زين بس الاسعار شوي غالية", "Arabic with slang"),

            # English with typos
            ("The mall is beutiful but expensiv", "English with typos"),

            # Mixed content
            ("المول beautiful والخدمة excellent بس expensive شوي", "Mixed Arabic-English"),

            # Clean English (should not change much)
            ("The mall is nice and clean", "Clean English"),

            # Common false positives we want to avoid
            ("Nice mall with good brands", "Clean English - should not change 'Nice' to 'Vice'"),
            ("Great malls in Riyadh", "Should not change 'malls' to 'walls'"),
            ("Halal restaurants available", "Should not change 'halal' to 'hall'"),
        ]

        for text, description in test_cases:
            print(f"\n--- Testing: {description} ---")
            print(f"Original: {text}")

            try:
                # Test language detection
                lang = self.detect_language(text)
                print(f"Detected language: {lang}")

                # Test processing
                processed = self.process_review_text(text)
                print(f"Processed: {processed}")

                # Show if there were changes
                if text != processed:
                    print("✓ Text was modified")
                else:
                    print("→ No changes made")

            except Exception as e:
                print(f"An error occurred: {e}")

        print("\n" + "="*50)
        print("TESTING COMPLETE")
        print("="*50)

    def show_random_samples(self, reviews, num_samples=5):
        """Show random samples of processed reviews"""
        if not reviews:
            return

        import random

        print(f"\n--- Random Sample of {min(num_samples, len(reviews))} Processed Reviews ---")
        print("-" * 60)

        sample_reviews = random.sample(reviews, min(num_samples, len(reviews)))

        for i, review in enumerate(sample_reviews, 1):
            print(f"\nSample {i}:")
            print(f"Name: {review['name']}")
            print(f"Date: {review['date']}")
            print(f"Rating: {review['rating']}")
            text_display = review['text'][:100] + "..." if len(review['text']) > 100 else review['text']
            print(f"Text: {text_display}")
            print("-" * 40)
        """Test preprocessing with sample texts"""
        print("\n" + "="*50)
        print("TESTING PREPROCESSING FUNCTIONALITY")
        print("="*50)

        test_cases = [
            # Arabic with slang
            ("المول زين بس الاسعار شوي غالية", "Arabic with slang"),

            # English with typos
            ("The mall is beutiful but expensiv", "English with typos"),

            # Mixed content
            ("المول beautiful والخدمة excellent بس expensive شوي", "Mixed Arabic-English"),

            # Clean English (should not change much)
            ("The mall is nice and clean", "Clean English"),

            # Common false positives we want to avoid
            ("Nice mall with good brands", "Clean English - should not change 'Nice' to 'Vice'"),
        ]

        for text, description in test_cases:
            print(f"\n--- Testing: {description} ---")
            print(f"Original: {text}")

            try:
                # Use the correct method name from your class
                processed = self.process_review_text(text)  # Changed from preprocess_text

                if processed != text:
                    print(f"Processed: {processed}")
                    print("✓ Text was modified")
                else:
                    print("✓ No changes needed")

            except Exception as e:
                print(f"❌ Error: {e}")

        # Test name cleaning too
        print(f"\n--- Testing Name Cleaning ---")
        test_names = [
            "mohammed ahmed",
            "SARAH SMITH",
            "محمد الأحمد",
            "john doe",
            "فاطمة العلي"
        ]

        for name in test_names:
            try:
                cleaned = self.clean_reviewer_name(name)
                if cleaned != name:
                    print(f"Name: {name} → {cleaned}")
                else:
                    print(f"Name: {name} (no change needed)")
            except Exception as e:
                print(f"❌ Name cleaning error for '{name}': {e}")


class GoogleMapsReviewScraper:
    def __init__(self):
        self.driver = None
        self.text_processor = ReviewTextProcessor()
        self.setup_driver()
        self.scrollable_div_selector = 'div.m6QErb.DQiDVb.ecceSd, div[aria-label="Reviews"], div[role="main"], div[jsaction*="pane.reviewList"]';
        self._cached_reviews_scroll_container = None

    def _extract_first_maps_url(self, value):
        """Return only the first valid Google Maps URL from any text blob."""
        if not value:
            return None
        s = str(value).strip()
        # Capture only URL-like tokens, avoiding full-dialog text pollution.
        match = re.search(
            r"(https?://(?:maps\.app\.goo\.gl|(?:www\.)?google\.com/maps)[^\s\"'<>]+)",
            s,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        url = match.group(1).strip().rstrip(").,;")
        return url if url else None

    def _is_valid_review_link(self, value):
        return self._extract_first_maps_url(value) is not None

    def _extract_link_from_share_dialog(self):
        """Fast extraction for both share dialog variants."""
        try:
            raw_candidate = self.driver.execute_script(
                """
                const candidates = [];
                const attrNames = ['value', 'href', 'data-url', 'data-link', 'data-share-url', 'aria-label'];
                const nodes = document.querySelectorAll('input, textarea, a[href], button, [role="button"], div');
                nodes.forEach(el => {
                  // visible attributes
                  attrNames.forEach(attr => {
                    const raw = (el.getAttribute && el.getAttribute(attr)) ? el.getAttribute(attr).trim() : '';
                    if (!raw) return;
                    if (raw.includes('maps.app.goo.gl') || raw.includes('google.com/maps') || raw.includes('/place/')) {
                      candidates.push(raw);
                    }
                  });
                  // element value/text
                  const val = (el.value || '').trim();
                  if (val && (val.includes('maps.app.goo.gl') || val.includes('google.com/maps') || val.includes('/place/'))) {
                    candidates.push(val);
                  }
                  const txt = (el.textContent || '').trim();
                  if (txt && (txt.includes('maps.app.goo.gl') || txt.includes('google.com/maps'))) {
                    candidates.push(txt);
                  }
                });
                return candidates.length ? candidates[0] : null;
                """
            )
            return self._extract_first_maps_url(raw_candidate)
        except Exception:
            return None

    def _try_copy_link_from_share_dialog(self):
        """Fallback: click Copy Link and read clipboard (JS or pyperclip)."""
        copy_xpaths = [
            "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'copy link')]",
            "//div[@role='button' and contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'copy link')]",
            "//span[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'copy link')]",
            "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'copy')]",
            "//div[@role='button' and contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'copy')]",
        ]

        copy_btn = None
        for xp in copy_xpaths:
            try:
                copy_btn = WebDriverWait(self.driver, 0.8).until(
                    EC.element_to_be_clickable((By.XPATH, xp))
                )
                break
            except TimeoutException:
                continue

        if not copy_btn:
            return None

        try:
            self.driver.execute_script("arguments[0].click();", copy_btn)
            time.sleep(0.15)
        except Exception:
            return None

        # First attempt: navigator.clipboard (works in some dialog variants)
        try:
            clip_value = self.driver.execute_async_script(
                """
                const done = arguments[0];
                if (!navigator.clipboard || !navigator.clipboard.readText) {
                  done(null);
                  return;
                }
                navigator.clipboard.readText().then(done).catch(() => done(null));
                """
            )
            clean = self._extract_first_maps_url(clip_value)
            if clean:
                return clean
        except Exception:
            pass

        # Second attempt: OS clipboard via pyperclip
        if PYPERCLIP_AVAILABLE:
            try:
                clip_value = PYPERCLIP_MODULE.paste()
                clean = self._extract_first_maps_url(clip_value)
                if clean:
                    return clean
            except Exception:
                pass

        return None

    def get_review_share_link(self, container):
        """
        Open the 3-dots menu for a review, click 'Share review',
        and read the per-review share link from the dialog.
        Supports both known Google share-dialog variants.
        """
        try:
            # Locate the 3-dots menu icon inside this review container
            menu_icon = container.find_element(By.CSS_SELECTOR, "span.eaLgGf.google-symbols")
        except NoSuchElementException:
            return None

        try:
            # Bring into view and click
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", menu_icon)
            time.sleep(0.2)
            try:
                self.driver.execute_script("arguments[0].click();", menu_icon)
            except Exception:
                # Fallback to ActionChains if JS click fails
                ActionChains(self.driver).move_to_element(menu_icon).click().perform()

            # Wait for and click the 'Share review' menu item (supports multiple menu markups)
            share_option = None
            share_option_xpaths = [
                "//div[@role='menuitemradio' and .//div[contains(@class,'mLuXec') and contains(., 'Share review')]]",
                "//div[@role='menuitem' and contains(., 'Share review')]",
                "//div[@role='menuitemradio' and contains(., 'Share')]",
                "//div[@role='menuitem' and contains(., 'Share')]",
            ]
            for xp in share_option_xpaths:
                try:
                    share_option = WebDriverWait(self.driver, 1.2).until(
                        EC.element_to_be_clickable((By.XPATH, xp))
                    )
                    break
                except TimeoutException:
                    continue

            if not share_option:
                return None

            self.driver.execute_script("arguments[0].click();", share_option)

            # Fast extraction: handle both dialog variants while the dialog animates in.
            link_value = None
            for _ in range(10):
                link_value = self._extract_link_from_share_dialog()
                if self._is_valid_review_link(link_value):
                    break
                time.sleep(0.15)

            # Fallback path: click "Copy Link" and read clipboard.
            if not self._is_valid_review_link(link_value):
                copied = self._try_copy_link_from_share_dialog()
                if self._is_valid_review_link(copied):
                    link_value = copied

            # Final short retry after copy click in case the dialog field gets populated.
            if not self._is_valid_review_link(link_value):
                for _ in range(4):
                    link_value = self._extract_link_from_share_dialog()
                    if self._is_valid_review_link(link_value):
                        break
                    time.sleep(0.1)

            # Best-effort close of the dialog so the next iterations are clean
            try:
                ActionChains(self.driver).send_keys(Keys.ESCAPE).perform()
            except Exception:
                pass

            clean_link = self._extract_first_maps_url(link_value)
            return clean_link if clean_link else None

        except Exception as e:
            print(f"DEBUG: Failed to get share link for a review: {e}")
            return None

    def setup_driver(self):
        """Setup Chrome driver with UTF-8 support for Arabic names"""
        chrome_options = Options()
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        chrome_options.add_argument("--lang=en-US")
        chrome_options.add_argument("--accept-lang=en-US,en")
        if os.getenv("SCRAPER_HEADLESS", "").strip().lower() in {"1", "true", "yes"}:
            # Required for CI runners (no display server).
            chrome_options.add_argument("--headless=new")
            chrome_options.add_argument("--window-size=1920,1080")

        try:
            self.driver = webdriver.Chrome(options=chrome_options)
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        except Exception as e:
            print(f"Error setting up Chrome driver: {e}")
            return None

    def modify_url_for_english(self, url):
        """Modify URL to force English interface"""
        parsed = urlparse(url)
        query_params = parse_qs(parsed.query)
        query_params['hl'] = ['en']
        query_params['gl'] = ['US']
        new_query = urlencode(query_params, doseq=True)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))

    def sort_by_newest(self):
        """Sort reviews by newest first"""
        try:
            print("Sorting reviews by newest first...")

            # Wait for and click the sort dropdown
            sort_button = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-value='Sort']"))
            )
            self.driver.execute_script("arguments[0].click();", sort_button)
            time.sleep(2)

            # Select "Newest" option
            newest_option = WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, "//div[@role='menuitemradio'][contains(., 'Newest')]"))
            )
            self.driver.execute_script("arguments[0].click();", newest_option)
            time.sleep(3)

            # After sorting, wait for the reviews to re-load in the sorted order
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div[data-review-id]"))
            )
            print("DEBUG: Reviews container re-loaded after sorting.")

            print("✓ Successfully sorted by newest reviews")
            return True

        except Exception as e:
            print(f"Could not sort by newest: {e}")
            return False

    def navigate_to_reviews_tab(self):
        """Navigates to the reviews tab if not already there."""
        try:
            # Find the tablist
            try:
                tablist = WebDriverWait(self.driver, 8).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "div[role='tablist']"))
                )
            except TimeoutException:
                # Tablist not found - return False so we can refresh
                return False
            
            # Check if already on reviews tab
            reviews_tab_selected = self.driver.find_elements(By.CSS_SELECTOR, "button[aria-label^='Reviews for'][aria-selected='true']")
            if reviews_tab_selected:
                print("Already on the reviews tab.")
                return True

            # Check if Overview or About tab is selected, then click Reviews
            overview_selected = self.driver.find_elements(By.CSS_SELECTOR, "button[aria-label^='Overview of'][aria-selected='true']")
            about_selected = self.driver.find_elements(By.CSS_SELECTOR, "button[aria-label^='About'][aria-selected='true']")
            
            if overview_selected or about_selected:
                reviews_tab_button = WebDriverWait(self.driver, 8).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "div[role='tablist'] button[aria-label*='Reviews']"))
                )
                self.driver.execute_script("arguments[0].click();", reviews_tab_button)
                time.sleep(2)
                print("✓ Navigated to reviews tab.")
                return True
            
            # Try to find and click Reviews tab directly
            reviews_tab_button = self.driver.find_elements(By.CSS_SELECTOR, "div[role='tablist'] button[aria-label*='Reviews']")
            if reviews_tab_button:
                self.driver.execute_script("arguments[0].click();", reviews_tab_button[0])
                time.sleep(2)
                print("✓ Clicked reviews tab.")
                return True
            
            return False
            
        except Exception as e:
            print(f"Error navigating to reviews tab: {e}")
            return False

    def click_more_buttons(self, num_reviews_target):
        """Clicks 'More' buttons to expand full review text."""
        print("DEBUG: Attempting to click 'More' buttons...") # Added debug log
        try:
            more_buttons_selectors = [
                "button.Jj6La", # Common button class for 'More'
                "button[aria-label^='See more']", # More descriptive aria-label
                "span.LMgQJb", # Some 'More' text might be in a span
                "button.wEwE6b.MDe0rd", # Updated selector found in recent Google Maps DOM (for 'Read more' or 'More')
                "button[jsaction*='reviews.expand']", # Specific button with expand action
                "g-review-controls > button", # Generic control button
                "span.google-symbols.Q1oZ3b" # Yet another observation
            ]
            
            total_clicked_count = 0
            # Dynamically calculate max_total_attempts based on the number of reviews target
            # Base attempts + 1 attempt for every 5 reviews, with a minimum of 10 and maximum of 100.
            max_total_attempts = max(10, min(100, 10 + (num_reviews_target // 5)))
            print(f"DEBUG: max_total_attempts for 'More' buttons set dynamically to {max_total_attempts}")

            for attempt_num in range(max_total_attempts):
                found_and_clicked_in_iteration = False
                current_iteration_clicks = 0

                for selector in more_buttons_selectors:
                    try:
                        buttons_to_click = self.driver.find_elements(By.CSS_SELECTOR, selector)
                        if buttons_to_click:
                            print(f"DEBUG: Found {len(buttons_to_click)} buttons with selector '{selector}' in attempt {attempt_num + 1}.")
                        
                        for button in buttons_to_click:
                            if button.is_displayed() and button.is_enabled():
                                try:
                                    self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
                                    time.sleep(0.2) # Small wait after scroll
                                    self.driver.execute_script("arguments[0].click();", button)
                                    found_and_clicked_in_iteration = True
                                    current_iteration_clicks += 1
                                    print(f"DEBUG: Clicked button with selector '{selector}' (Attempt {attempt_num + 1}).")
                                    time.sleep(0.5) # Short delay after each click to allow content to load
                                except ElementClickInterceptedException:
                                    print(f"DEBUG: Click intercepted for selector '{selector}', trying ActionChains.")
                                    ActionChains(self.driver).move_to_element(button).click().perform()
                                    found_and_clicked_in_iteration = True
                                    current_iteration_clicks += 1
                                    print(f"DEBUG: Clicked button with ActionChains for selector '{selector}' (Attempt {attempt_num + 1}).")
                                    time.sleep(0.5)
                                except Exception as e:
                                    print(f"WARNING: Error clicking button with selector {selector}: {e}")
                                    continue
                    except Exception as e:
                        print(f"WARNING: Error finding buttons with selector {selector}: {e}") # Debug for selector issues
                        continue
                
                if found_and_clicked_in_iteration:
                    total_clicked_count += current_iteration_clicks
                    # No 'attempts_without_new_clicks' logic here, as we want to keep trying to expand all until no more are found
                else:
                    # If no buttons were found OR clicked in this entire iteration, it's a sign to stop
                    print(f"DEBUG: No 'More' buttons found or clicked in attempt {attempt_num + 1}. Stopping click loop.")
                    break
                
                time.sleep(1) # Small delay before next iteration to allow page to settle
            
            print(f"DEBUG: Finished clicking 'More' buttons. Total expanded: {total_clicked_count}")
        except Exception as e:
            print(f"CRITICAL ERROR in click_more_buttons: {e}")

    def extract_reviews_from_page(self, seen_ids=None):
        reviews_on_page = []
        review_containers = self.driver.find_elements(By.CSS_SELECTOR, "div[data-review-id]")

        for container in review_containers:
            review_data = {
                'name': 'N/A',
                'date': 'N/A',
                'rating': 'N/A',
                'text': 'N/A',
                'id': 'N/A',  # Unique ID for each review
                'link': 'N/A'  # Per-review share link
            }

            try:
                review_data['id'] = container.get_attribute("data-review-id")

                # If we've already seen this review ID in a previous scroll, skip it entirely
                if seen_ids is not None and review_data['id']:
                    if review_data['id'] in seen_ids:
                        continue
                    # Mark this ID as seen so we don't process it again in later scrolls
                    seen_ids.add(review_data['id'])

                try:
                    name_element = container.find_element(By.CSS_SELECTOR, "div.d4r55.fontTitleMedium")
                    review_data['name'] = name_element.text.strip()
                except:
                    pass

                try:
                    date_element = container.find_element(By.CSS_SELECTOR, "div.DU9Pgb span.rsqaWe")
                    review_data['date'] = date_element.text.strip()
                except:
                    pass

                try:
                    rating_element = container.find_element(By.CSS_SELECTOR, "div.DU9Pgb span.kvMYJc[role='img']")
                    aria_label = rating_element.get_attribute("aria-label")
                    if aria_label:
                        rating_match = re.search(r'(\d+)', aria_label)
                        if rating_match:
                            review_data['rating'] = rating_match.group(1) + " stars"
                except:
                    pass

                try:
                    text_element = container.find_element(By.CSS_SELECTOR, "div.MyEned span.wiI7pd")
                    review_data['text'] = text_element.text.strip()
                except:
                    pass

                # Try to fetch the per-review share link via the 3-dots menu → Share review → Copy link dialog
                try:
                    link_value = self.get_review_share_link(container)
                    if link_value:
                        review_data['link'] = link_value
                        print(f"DEBUG: Retrieved share link for review {review_data.get('id')}: {link_value}")
                except Exception as e:
                    print(f"DEBUG: Error while retrieving share link for review {review_data.get('id')}: {e}")

                reviews_on_page.append(review_data)
            except Exception as e:
                print(f"Error extracting review from container: {e}")
                continue
        return reviews_on_page

    def scroll_reviews(self):
        """Performs a single scroll action on the reviews pane."""
        print("DEBUG: Performing a single scroll action...")
        try:
            scrollable_div = self.get_reviews_scroll_container()

            if scrollable_div:
                print("DEBUG: Scrolling detected reviews scroll container.")
                # Scroll inside the actual reviews pane (this is what triggers lazy-load reliably)
                self.driver.execute_script(
                    "arguments[0].scrollTop = arguments[0].scrollTop + Math.max(600, arguments[0].clientHeight);",
                    scrollable_div
                )
                time.sleep(1.2)
                self.driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", scrollable_div)
                time.sleep(1.2)
            else:
                # Fallback: general page scroll (less reliable for loading more reviews)
                print("DEBUG: Could not detect reviews container; falling back to page scroll.")
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(1.5)
        except Exception as e:
            print(f"ERROR during single scroll action: {e}")

    def get_reviews_scroll_container(self):
        """
        Find the real scrollable container that holds the reviews list.
        Scrolling the wrong container (e.g. div[role='main']) often stops lazy-loading at ~50-80 reviews.
        """
        if self._cached_reviews_scroll_container is not None:
            try:
                # Accessing a property will throw if stale
                _ = self._cached_reviews_scroll_container.tag_name
                return self._cached_reviews_scroll_container
            except Exception:
                self._cached_reviews_scroll_container = None

        # Strategy A: find the first review element, then walk up to the first scrollable ancestor.
        try:
            first_review = self.driver.find_element(By.CSS_SELECTOR, "div[data-review-id]")
            scrollable = self.driver.execute_script(
                """
                let el = arguments[0];
                while (el && el !== document.body) {
                  const sh = el.scrollHeight || 0;
                  const ch = el.clientHeight || 0;
                  if (sh > ch + 50) return el;
                  el = el.parentElement;
                }
                return null;
                """,
                first_review
            )
            if scrollable:
                self._cached_reviews_scroll_container = scrollable
                return scrollable
        except Exception:
            pass

        # Strategy B: try known selectors (kept as fallback)
        selectors_to_try = [s.strip() for s in self.scrollable_div_selector.split(',') if s.strip()]
        for selector in selectors_to_try:
            try:
                el = WebDriverWait(self.driver, 2).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                )
                # Only accept if it's actually scrollable
                is_scrollable = self.driver.execute_script(
                    "return (arguments[0].scrollHeight || 0) > (arguments[0].clientHeight || 0) + 50;",
                    el
                )
                if is_scrollable:
                    print(f"DEBUG: Reviews scroll container selected by selector: {selector}")
                    self._cached_reviews_scroll_container = el
                    return el
            except Exception:
                continue

        return None

    def preprocess_reviews(self, reviews):
        """Preprocess all reviews using CAMeL Tools and TextBlob"""
        if not reviews:
            return reviews

        print("\n" + "="*50)
        print("PREPROCESSING REVIEWS WITH CAMEL TOOLS")
        print("="*50)

        processed_reviews = []

        for i, review in enumerate(reviews, 1):
            print(f"\nProcessing review {i}/{len(reviews)}...")

            processed_review = review.copy()

            # Process reviewer name
            original_name = review['name']
            processed_review['name'] = self.text_processor.clean_reviewer_name(original_name)
            if original_name != processed_review['name']:
                print(f"  Name: {original_name} → {processed_review['name']}")

            # Process review text
            original_text = review['text']
            processed_review['text'] = self.text_processor.process_review_text(original_text)
            if original_text != processed_review['text'] and len(original_text) > 0:
                print(f"  Text: {original_text[:50]}... → {processed_review['text'][:50]}...")

            processed_reviews.append(processed_review)

        print(f"\n✓ Successfully preprocessed {len(processed_reviews)} reviews!")
        return processed_reviews


    def wait_for_page_load(self, timeout=10):
        """Wait for the page to be fully loaded"""
        try:
            WebDriverWait(self.driver, timeout).until(
                lambda driver: driver.execute_script("return document.readyState") == "complete"
            )
            time.sleep(2)  # Brief wait for Google Maps to initialize
            return True
        except TimeoutException:
            time.sleep(2)
            return False

    def scrape_reviews(self, url, num_reviews):
        """Main scraping function with newest first sorting"""
        if not self.driver:
            print("Driver not initialized")
            return []

        try:
            # Modify URL for English interface
            english_url = self.modify_url_for_english(url)
            print("Opening URL...")

            # Load the new URL (retry once if ChromeDriver disconnects)
            try:
                self.driver.get(english_url)
                self.wait_for_page_load()
            except Exception as e:
                msg = str(e).lower()
                if "invalid session id" in msg or "not connected to devtools" in msg or "disconnected" in msg:
                    print("WARNING: Browser session disconnected. Recreating driver and retrying once...")
                    try:
                        self.close()
                    except Exception:
                        pass
                    self.setup_driver()
                    if not self.driver:
                        return []
                    self.driver.get(english_url)
                    self.wait_for_page_load()
                else:
                    raise
            
            # Try to navigate to reviews tab
            if not self.navigate_to_reviews_tab():
                # If tabs not found, refresh once and try again
                print("Reviews tab not found. Refreshing page...")
                self.driver.refresh()
                self.wait_for_page_load()
                if not self.navigate_to_reviews_tab():
                    print("Failed to navigate to reviews tab. Aborting scrape.")
                    return []

            # Wait for reviews to be present
            try:
                WebDriverWait(self.driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "div[data-review-id]"))
                )
                print("Reviews found on page")
            except TimeoutException:
                print("No reviews found on this page")
                return []

            # Sort by newest first
            self.sort_by_newest()
            time.sleep(3)

            all_extracted_reviews = []
            seen_reviews_ids = set()
            no_new_reviews_attempts = 0
            # Dynamically calculate MAX_NO_NEW_REVIEWS_ATTEMPTS based on num_reviews
            # Base attempts + 1 attempt for every 10 reviews, with a minimum of 5 and maximum of 50.
            MAX_NO_NEW_REVIEWS_ATTEMPTS = max(5, min(50, 5 + (num_reviews // 10)))
            print(f"DEBUG: MAX_NO_NEW_REVIEWS_ATTEMPTS set dynamically to {MAX_NO_NEW_REVIEWS_ATTEMPTS}")

            while len(all_extracted_reviews) < num_reviews:
                self.scroll_reviews() # Perform a scroll action
                time.sleep(2) # Give time for content to load

                # Call click_more_buttons with the target number of reviews
                self.click_more_buttons(num_reviews)

                # Extract only new reviews after each scroll
                current_page_reviews = self.extract_reviews_from_page(seen_reviews_ids)

                if not current_page_reviews:
                    # Only increment when we truly found no NEW unique reviews.
                    # (Do NOT also increment on scrollHeight changes; that caused premature stopping.)
                    no_new_reviews_attempts += 1
                    print(f"DEBUG: No new unique reviews found on this scroll. Attempt {no_new_reviews_attempts}/{MAX_NO_NEW_REVIEWS_ATTEMPTS}.")
                    # Give Google a bit more time to lazy-load before we declare "stuck"
                    time.sleep(2.5)
                    if no_new_reviews_attempts >= MAX_NO_NEW_REVIEWS_ATTEMPTS:
                        print("DEBUG: No new unique reviews after several attempts. Stopping.")
                        break
                else:
                    all_extracted_reviews.extend(current_page_reviews)
                    # Reset no_new_reviews_attempts here if new unique reviews were found, regardless of scroll height
                    no_new_reviews_attempts = 0
                    print(f"DEBUG: Found {len(current_page_reviews)} new unique reviews. Total collected: {len(all_extracted_reviews)}")

                # Ensure we don't exceed the target number of reviews
                if len(all_extracted_reviews) >= num_reviews:
                    all_extracted_reviews = all_extracted_reviews[:num_reviews]
                    print(f"DEBUG: Target of {num_reviews} unique reviews reached.")
                    break

            print(f"Finished scraping. Total unique reviews collected: {len(all_extracted_reviews)}")
            return all_extracted_reviews

        except Exception as e:
            print(f"Error during scraping: {e}")
            return []

    def scrape_reviews_recent_with_text(self, url, days_back: int):
        """
        Collect reviews from the last `days_back` calendar days that include written text
        (skip star-only / empty). Stops scrolling once any review with a parseable date
        older than the window is seen (newest-first order).
        """
        if not self.driver:
            print("Driver not initialized")
            return []

        try:
            days_back = max(1, int(days_back))
        except (TypeError, ValueError):
            days_back = 7

        try:
            english_url = self.modify_url_for_english(url)
            print(f"Opening URL (last {days_back} day(s), in-window reviews with text only)...")

            try:
                self.driver.get(english_url)
                self.wait_for_page_load()
            except Exception as e:
                msg = str(e).lower()
                if "invalid session id" in msg or "not connected to devtools" in msg or "disconnected" in msg:
                    print("WARNING: Browser session disconnected. Recreating driver and retrying once...")
                    try:
                        self.close()
                    except Exception:
                        pass
                    self.setup_driver()
                    if not self.driver:
                        return []
                    self.driver.get(english_url)
                    self.wait_for_page_load()
                else:
                    raise

            if not self.navigate_to_reviews_tab():
                print("Reviews tab not found. Refreshing page...")
                self.driver.refresh()
                self.wait_for_page_load()
                if not self.navigate_to_reviews_tab():
                    print("Failed to navigate to reviews tab. Aborting scrape.")
                    return []

            try:
                WebDriverWait(self.driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "div[data-review-id]"))
                )
                print("Reviews found on page")
            except TimeoutException:
                print("No reviews found on this page")
                return []

            self.sort_by_newest()
            time.sleep(3)

            cutoff = datetime.now() - timedelta(days=days_back)
            collected = []
            seen_reviews_ids = set()
            no_new_reviews_attempts = 0
            MAX_NO_NEW_REVIEWS_ATTEMPTS = 15
            stop_past_window = False
            more_target = 500

            print(
                f"DEBUG: Date-window cutoff (>= this time, last {days_back} day(s)): "
                f"{cutoff.isoformat(timespec='seconds')}"
            )

            while not stop_past_window:
                self.scroll_reviews()
                time.sleep(2)
                self.click_more_buttons(more_target)

                current_page_reviews = self.extract_reviews_from_page(seen_reviews_ids)

                if not current_page_reviews:
                    no_new_reviews_attempts += 1
                    print(
                        f"DEBUG: No new unique reviews on scroll. "
                        f"Attempt {no_new_reviews_attempts}/{MAX_NO_NEW_REVIEWS_ATTEMPTS}."
                    )
                    time.sleep(2.5)
                    if no_new_reviews_attempts >= MAX_NO_NEW_REVIEWS_ATTEMPTS:
                        print("DEBUG: Stopping — no more reviews loading.")
                        break
                    continue

                no_new_reviews_attempts = 0

                for rev in current_page_reviews:
                    parsed = parse_google_maps_review_date(rev.get("date"))
                    if parsed is None:
                        print(f"DEBUG: Skip (could not parse date: {rev.get('date')!r})")
                        continue

                    if parsed < cutoff:
                        print(
                            f"DEBUG: Review dated {rev.get('date')!r} is before cutoff; "
                            "stopping (older reviews follow in newest-first order)."
                        )
                        stop_past_window = True
                        break

                    if not review_has_meaningful_text(rev.get("text")):
                        print(
                            f"DEBUG: Skip rating-only in window "
                            f"({rev.get('name', 'N/A')}, {rev.get('date')!r})"
                        )
                        continue

                    collected.append(rev)
                    print(
                        f"DEBUG: Kept in-window review with text "
                        f"({rev.get('name', 'N/A')}, {rev.get('date')!r}, "
                        f"parsed={parsed.isoformat(timespec='seconds')})"
                    )

            print(
                f"Finished date-window scrape. Reviews with text in the last {days_back} day(s): {len(collected)}"
            )
            return collected

        except Exception as e:
            print(f"Error during date-window scraping: {e}")
            return []

    def scrape_reviews_last_week_with_text(self, url):
        """Same as scrape_reviews_recent_with_text(url, 7)."""
        return self.scrape_reviews_recent_with_text(url, 7)

    def save_to_csv(self, reviews, filename="google_maps_reviews.csv"):
        """Save reviews to CSV file with UTF-8 encoding"""
        if not reviews:
            print("No reviews to save")
            return

        with open(filename, 'w', newline='', encoding='utf-8-sig') as csvfile:
            # Include the per-review share link in the CSV output
            fieldnames = ['name', 'date', 'rating', 'text', 'link']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

            writer.writeheader()
            for review in reviews:
                writer.writerow(review)

        print(f"Reviews saved to {filename}")

    def close(self):
        """Close the driver"""
        if self.driver:
            self.driver.quit()


def detect_review_language(text):
    """Detect if review is Arabic, English, or mixed"""
    if not text or text == 'N/A':
        return 'unknown'

    # Count Arabic and English characters
    arabic_chars = len(re.findall(r'[\u0600-\u06FF]', text))
    english_chars = len(re.findall(r'[a-zA-Z]', text))

    total_chars = arabic_chars + english_chars

    if total_chars == 0:
        return 'unknown'

    arabic_ratio = arabic_chars / total_chars
    english_ratio = english_chars / total_chars

    # Classification thresholds
    if arabic_ratio > 0.7:
        return 'arabic'
    elif english_ratio > 0.7:
        return 'english'
    elif arabic_ratio > 0.2 and english_ratio > 0.2:
        return 'mixed'
    else:
        return 'unknown'

        
def main():
    print("Google Maps Review Scraper with CAMeL Tools - Newest First")
    print("-" * 60)

    # Check dependencies
    missing_deps = []
    if not CAMEL_AVAILABLE:
        missing_deps.append("camel-tools")
    if not TEXTBLOB_AVAILABLE:
        missing_deps.append("textblob")
    if not LANGDETECT_AVAILABLE:
        missing_deps.append("langdetect")

    if missing_deps:
        print(f"Optional dependencies missing: {', '.join(missing_deps)}")
        print("Install with: pip install " + " ".join(missing_deps))
        print("Continuing with available features...\n")

    # Initialize processor once
    processor = ReviewTextProcessor()

    # Get input from user
    url = input("Enter the Google Maps place URL: ").strip()

    try:
        num_reviews = int(input("Enter the number of reviews to scrape: "))
    except ValueError:
        print("Invalid number. Using default value of 10.")
        num_reviews = 10

    # Initialize scraper
    scraper = GoogleMapsReviewScraper()

    try:
        # Scrape reviews (newest first)
        reviews = scraper.scrape_reviews(url, num_reviews)

        if reviews:
            # Add testing option BEFORE processing
            test_choice = input("\nRun preprocessing tests? (y/n): ").lower()
            if test_choice == 'y':
                processor.test_preprocessing()

            # Process reviews
            processed_reviews = processor.preprocess_reviews(reviews)

            print(f"\nSuccessfully scraped and processed {len(processed_reviews)} reviews!")

            # Display first few reviews as preview
            print("\nPreview of processed reviews (newest first):")
            print("-" * 50)
            for i, review in enumerate(processed_reviews[:3], 1):
                print(f"Review {i}:")
                print(f"Name: {review['name']}")
                print(f"Date: {review['date']}")
                print(f"Rating: {review['rating']}")
                print(f"Text: {review['text'][:100]}..." if len(review['text']) > 100 else f"Text: {review['text']}")
                print("-" * 50)

            # Save to CSV
            filename = input("\nEnter filename for CSV (press Enter for default): ").strip()
            if not filename:
                filename = "google_maps_reviews.csv"

            # Save both original and processed versions option
            save_choice = input("\nSave both original and processed versions? (y/n): ").lower()
            if save_choice == 'y':
                scraper.save_to_csv(reviews, "original_reviews.csv")
                scraper.save_to_csv(processed_reviews, "processed_reviews.csv")
                print("Saved both versions!")
            else:
                scraper.save_to_csv(processed_reviews, filename)

        else:
            print("No reviews were scraped. Please check the URL and try again.")

    except KeyboardInterrupt:
        print("\nScraping interrupted by user.")
    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        scraper.close()

    if reviews:
        # Add testing option
        test_choice = input("\nRun preprocessing tests? (y/n): ").lower()
        if test_choice == 'y':
            processor = ReviewTextProcessor()
            processor.test_preprocessing()

        # Process reviews
        processed_reviews = processor.preprocess_reviews(reviews)

        # Show samples
        processor.show_random_samples(processed_reviews)

        # Save both original and processed versions
        save_choice = input("\nSave both original and processed versions? (y/n): ").lower()
        if save_choice == 'y':
            scraper.save_to_csv(reviews, "original_reviews.csv")
            scraper.save_to_csv(processed_reviews, "processed_reviews.csv")
        else:
            scraper.save_to_csv(processed_reviews, filename)

def scrape_reviews_function(url, num_reviews):
    """Standalone function to scrape reviews"""
    scraper = None
    try:
        print(f"\n{'='*60}")
        print(f"Starting new scraping session...")
        print(f"{'='*60}")
        
        scraper = GoogleMapsReviewScraper()
        
        # Small delay to ensure driver is fully ready
        time.sleep(1)
        
        reviews = scraper.scrape_reviews(url, num_reviews)
        
        print(f"\n{'='*60}")
        print(f"Scraping session completed. Reviews collected: {len(reviews) if reviews else 0}")
        print(f"{'='*60}\n")
        
        return reviews
    except Exception as e:
        import traceback
        print(f"\n{'='*60}")
        print(f"ERROR in scraping: {e}")
        print(f"Traceback:")
        traceback.print_exc()
        print(f"{'='*60}\n")
        return []
    finally:
        if scraper:
            try:
                scraper.close()
                print("✓ Browser closed successfully")
            except Exception as e:
                print(f"Warning: Error closing browser: {e}")


def scrape_reviews_function_last_week(url):
    """Scrape only reviews from the last 7 days that include text (newest-first list)."""
    return scrape_reviews_function_recent_with_text(url, 7)


def scrape_reviews_function_recent_with_text(url, days_back: int):
    """Scrape reviews from the last `days_back` days that include text (newest-first list)."""
    scraper = None
    try:
        print(f"\n{'='*60}")
        print(f"Starting date-window (with text) scraping session — last {days_back} day(s)...")
        print(f"{'='*60}")

        scraper = GoogleMapsReviewScraper()
        time.sleep(1)

        reviews = scraper.scrape_reviews_recent_with_text(url, days_back)

        print(f"\n{'='*60}")
        print(f"Scraping session completed. Matching reviews: {len(reviews) if reviews else 0}")
        print(f"{'='*60}\n")

        return reviews
    except Exception as e:
        import traceback

        print(f"\n{'='*60}")
        print(f"ERROR in scraping: {e}")
        print("Traceback:")
        traceback.print_exc()
        print(f"{'='*60}\n")
        return []
    finally:
        if scraper:
            try:
                scraper.close()
                print("✓ Browser closed successfully")
            except Exception as e:
                print(f"Warning: Error closing browser: {e}")


def process_reviews_function(reviews):
    """Standalone function to process reviews"""
    processor = ReviewTextProcessor()
    try:
        processed_reviews = processor.preprocess_reviews(reviews)
        return processed_reviews
    except Exception as e:
        print(f"Error in processing: {e}")
        return reviews

def save_reviews_function(reviews, filename):
    """Standalone function to save reviews"""
    scraper = GoogleMapsReviewScraper()
    try:
        scraper.save_to_csv(reviews, filename)
        return True
    except Exception as e:
        print(f"Error saving: {e}")
        return False

# Make classes available for import
__all__ = [
    "GoogleMapsReviewScraper",
    "ReviewTextProcessor",
    "scrape_reviews_function",
    "scrape_reviews_function_last_week",
    "scrape_reviews_function_recent_with_text",
    "process_reviews_function",
    "save_reviews_function",
    "parse_google_maps_review_date",
    "review_has_meaningful_text",
]

# Only run main() if script is executed directly
if __name__ == "__main__":
    main()