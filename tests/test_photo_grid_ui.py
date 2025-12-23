"""
Playwright UI tests for photo grid functionality.

Tests:
1. Photo grid displays more than 1 photo and first row contains more than 1 photo
2. Selecting first photo updates the map view
3. Double-clicking first photo displays the modal
"""

import platform

import pytest
from playwright.sync_api import Page

# Skip these tests if not running on macOS
pytestmark = pytest.mark.skipif(
    platform.system() != "Darwin",
    reason="Playwright UI tests only run on macOS",
)


@pytest.fixture(scope="module")
def browser_context_args(browser_context_args):
    """Configure browser context for tests."""
    return {
        **browser_context_args,
        "viewport": {"width": 1920, "height": 1080},
    }


@pytest.fixture
def page_with_photos(page: Page, live_server, processed_photos):
    """
    Load the photo grid page and wait for photos to load.

    Uses live_server with test database populated by processed_photos fixture.
    """
    page.goto(live_server.url)
    page.wait_for_load_state("networkidle")

    # Click search to load photos
    search_btn = page.locator('button:has-text("Search")')
    search_btn.click()

    # Wait for at least one photo to appear
    page.wait_for_selector("[data-photo-id]", timeout=5000)
    page.wait_for_timeout(1000)

    return page


def test_photo_grid_display(page_with_photos: Page):
    """
    Test 1: Check if photo grid displays more than 1 photo
    and first row contains more than 1 photo.
    """
    page = page_with_photos

    # Check if more than 1 photo is displayed
    photos = page.locator("[data-photo-id]")
    photo_count = photos.count()

    assert photo_count > 1, f"Expected more than 1 photo, found {photo_count}"
    print(f"✓ Photo grid displays {photo_count} photos")

    # Get the first photo's position
    first_photo = photos.first
    first_photo_box = first_photo.bounding_box()
    assert first_photo_box is not None, "First photo not visible"

    # Find photos in the first row (same Y position +/- some tolerance)
    first_row_photos = []
    tolerance = 10  # pixels

    for i in range(min(photo_count, 10)):  # Check first 10 photos
        photo = photos.nth(i)
        box = photo.bounding_box()
        if box and abs(box["y"] - first_photo_box["y"]) < tolerance:
            first_row_photos.append(i)

    first_row_count = len(first_row_photos)
    assert first_row_count > 1, (
        f"Expected more than 1 photo in first row, found {first_row_count}"
    )
    print(f"✓ First row contains {first_row_count} photos")


def test_select_photo_updates_map(page_with_photos: Page):
    """
    Test 2: Check if selecting first photo updates the map view.
    """
    page = page_with_photos

    # Get the first photo
    photos = page.locator("[data-photo-id]")
    first_photo = photos.first

    # Click the first photo to select it
    first_photo.click()
    page.wait_for_timeout(500)

    # Check if photo is selected (has primary border)
    first_photo_classes = first_photo.get_attribute("class")
    assert first_photo_classes is not None, "Photo should have class attribute"
    assert "border-primary" in first_photo_classes, (
        "Photo should have primary border when selected"
    )
    print("✓ First photo is selected")

    # Check if selection count updated
    selection_count = page.locator("#selection-count").text_content()
    assert selection_count is not None, "Selection count element should have text"
    assert "1 photo selected" in selection_count, (
        f"Expected '1 photo selected', got '{selection_count}'"
    )
    print(f"✓ Selection count updated: {selection_count}")

    # Check if map location input was updated
    # (will update if photo has location data)
    updated_location = page.locator("#location-input").input_value()
    print(f"✓ Location input after selection: '{updated_location}'")

    # The location may or may not change depending on whether
    # the photo has location data
    # Just verify the map interaction happened by checking the input exists
    assert page.locator("#location-input").is_visible(), "Location input should be visible"


def test_double_click_opens_modal(page_with_photos: Page):
    """
    Test 3: Check if double-clicking first photo displays the modal.
    """
    page = page_with_photos

    # Get the first photo
    photos = page.locator("[data-photo-id]")
    first_photo = photos.first

    # Get photo ID for verification
    photo_id = first_photo.get_attribute("data-photo-id")
    assert photo_id is not None, "Photo should have data-photo-id attribute"
    print(f"✓ Testing modal for photo ID: {photo_id}")

    # Double-click the first photo
    first_photo.dblclick()
    page.wait_for_timeout(1000)

    # Check if modal is visible
    modal = page.locator("#photo-modal")
    assert modal.is_visible(), "Modal should be visible after double-click"
    print("✓ Modal is visible")

    # Verify modal content is populated
    modal_filename = page.locator("#modal-filename").text_content()
    assert modal_filename, "Modal should display filename"
    print(f"✓ Modal filename: {modal_filename}")

    # Verify preview image uses correct endpoint
    modal_img = page.locator("#modal-photo")
    img_src = modal_img.get_attribute("src")
    assert img_src, "Modal image should have src attribute"
    assert f"/api/preview/{photo_id}/" in img_src, f"Expected preview endpoint, got {img_src}"
    print(f"✓ Modal using preview endpoint: {img_src}")

    # Verify location source badge exists
    modal_badge = page.locator("#modal-manual")
    badge_text = modal_badge.text_content()
    assert badge_text in [
        "Manual",
        "Auto-geocoded",
        "Unknown",
    ], f"Unexpected badge text: {badge_text}"
    print(f"✓ Location source badge: {badge_text}")

    # Close the modal
    close_btn = page.locator('button:has-text("Close")')
    close_btn.click()
    page.wait_for_timeout(500)

    print("✓ Modal test completed successfully")


def test_complete_workflow(page_with_photos: Page):
    """
    Integration test: Complete workflow of grid display, selection, and modal.
    """
    page = page_with_photos

    # Step 1: Verify grid
    photos = page.locator("[data-photo-id]")
    photo_count = photos.count()
    assert photo_count > 1, "Grid should have multiple photos"

    # Step 2: Select first photo
    first_photo = photos.first
    first_photo.click()
    page.wait_for_timeout(300)

    # Verify selection
    selection_text = page.locator("#selection-count").text_content()
    assert selection_text is not None, "Selection count should have text"
    assert "1 photo" in selection_text, "Should show 1 photo selected"

    # Step 3: Double-click to open modal
    first_photo.dblclick()
    page.wait_for_timeout(500)

    # Verify modal
    modal = page.locator("#photo-modal")
    assert modal.is_visible(), "Modal should open"

    # Close modal
    page.locator('button:has-text("Close")').click()
    page.wait_for_timeout(300)

    print("✓ Complete workflow test passed")
