from atproto import Client
import time
import os

USERNAME = os.getenv("BSKY_USR")
PASSWORD = os.getenv("BSKY_PWD")

# Initialize the client and authenticate
client:Client = Client()

def init():
    global client
    client.login(USERNAME, PASSWORD)
    pass

def get_target_did(user_handle):
    if not client.me:
        init()
    target_user = client.app.bsky.actor.get_profile({'actor': user_handle})
    target_did = target_user.did
    return target_did

def check_for_new_post(target_did):
    if not client.me:
        init()
    # Fetch the latest posts from the user
    timeline = client.app.bsky.feed.get_author_feed({'actor': target_did, 'limit': 1})
    
    if timeline.feed:
        latest_post = timeline.feed[0].post
        return latest_post        

def check_for_new_posts(user_handle):
    if not client.me:
        init()
    # Get the DID (Decentralized Identifier) of the target user
    target_user = client.app.bsky.actor.get_profile({'actor': user_handle})
    target_did = target_user.did
    print(f"Monitoring posts from {user_handle} (DID: {target_did})")
    # Keep track of the latest post
    last_post = None

    while True:
        try:
            # Fetch the latest posts from the user
            timeline = client.app.bsky.feed.get_author_feed({'actor': target_did, 'limit': 1})
            
            if timeline.feed:
                latest_post = timeline.feed[0].post
                
                # Check if it's a new post
                if last_post is None or latest_post.cid != last_post.cid:
                    print(f"New post detected: {latest_post.record.text}")
                    last_post = latest_post  # Update last seen post
                    yield latest_post
                    
            time.sleep(10)  # Adjust the polling interval as needed
        except Exception as e:
            print(f"Error occurred: {e}")
            break  # Stop if there's an issue (e.g., network failure)

if __name__ == "__main__":
    USER_HANDLE = "arenadailydeals.bsky.social"  # The user you want to monitor
    did = get_target_did(USER_HANDLE)
    post = check_for_new_post(did)
    print(post)