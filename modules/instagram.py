import os
from apify_client import ApifyClient
import time

def get_latest_instagram_posts(profile_username: str, max_posts: int = 5, apify_token: str = None):
    """
    Uses Apify's Instagram Scraper to fetch the latest posts from a public Instagram profile.

    :param profile_username: Instagram handle (e.g., 'nasa')
    :param max_posts: Max number of recent posts to return
    :param apify_token: Your Apify API token
    :return: List of dictionaries with post details
    """
    if apify_token is None:
        raise ValueError("Apify API token must be provided")

    client = ApifyClient(apify_token)

    # Start the Instagram Scraper actor
    run = client.actor("apify/instagram-scraper").call(run_input={
        "addParentData": False,
        "directUrls": [
            f"https://www.instagram.com/{profile_username}"
        ],
        "isUserReelFeedURL": False,
        "isUserTaggedFeedURL": False,
        "resultsLimit": max_posts,
        "resultsType": "posts",
        "searchLimit": 1,
    })

    # Wait briefly to ensure dataset is ready
    time.sleep(5)

    # Fetch the results (this automatically fetches dataset items)
    dataset_items = client.dataset(run["defaultDatasetId"]).list_items().items

    # Return structured post data
    return dataset_items

# alle zwei Stunden abfragen
if __name__ == "__main__":
    # Example usage
    apify_token = os.getenv("API_KEY_INSTAGRAM")
    try:
        posts = get_latest_instagram_posts("gamerii93", max_posts=5, apify_token=apify_token)
        for post in posts:
            print(f"is pinned: {post.get('isPinned', False)}")
            print(f"Post ID: {post['id']}")
            print(f"Caption: {post['caption']}")
            print(f"Image URL: {post['image_url']}")
            print(f"Timestamp: {post['timestamp']}")
            print(f"Post URL: {post['post_url']}")
            print("-" * 40)
    except Exception as e:
        print(f"Error fetching posts: {e}")