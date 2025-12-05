from litestar import Litestar, post, get, Request, Response, MediaType
import uvicorn
import asyncio
from logger_config import console_logger, file_logger
import psycopg2
import os
from dotenv import load_dotenv
from review_code import build_code_review_graph

# Queues for requests
review_queue = asyncio.Queue()

async def process_review_queue():
    while True:
        data = await review_queue.get()
        event_type = data["event_type"]
        pr_id = data["json"]["pullRequest"]["id"]
        repo = data["json"]["pullRequest"]["toRef"]["repository"]["slug"]
        project = data["json"]["pullRequest"]["toRef"]["repository"]["project"]["key"]

        console_logger.info(
            "Processing post request for code review...", 
            event_type=event_type,
            pull_request=(project, repo, pr_id)
        )
        file_logger.info(
            "Processing post request for code review...", 
            event_type=event_type,
            pull_request=(project, repo, pr_id)
        )

        try: 
            await build_code_review_graph(pr_id, repo, project, console_logger, file_logger),
        except Exception as e:
            console_logger.exception(f"Code review process failed: {e}", pull_request=(project, repo, pr_id))
            file_logger.exception(f"Code review process failed: {e}", pull_request=(project, repo, pr_id))

        review_queue.task_done()

def save_feedback(score: int, comment_id: str):
    load_dotenv()
    conn = psycopg2.connect(
        dbname=os.environ.get('POSTGRES_DB'),
        user=os.environ.get('POSTGRES_USER'),
        password=os.environ.get('POSTGRES_PASSWORD'),
        host='sentinel_db',
        port='5432'
    )
    cursor = conn.cursor()

    # Create the feedback table if it doesn't exist
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS feedback (
            comment_id TEXT,
            score INTEGER DEFAULT 0,
            num_reviews INTEGER DEFAULT 0
        )
        """
    )

    # Insert comment information if it does not exist
    cursor.execute(
        """
        SELECT 1 FROM feedback 
        WHERE comment_id = %s
        """,
        (comment_id,)
    )
    exists = cursor.fetchone()
    if not exists:
        cursor.execute(
            """
            INSERT INTO feedback (comment_id, score, num_reviews)
            VALUES (%s, 0, 0)
            """, 
            (comment_id,)
        )

    # Update score and num_reviewers
    cursor.execute(
        """
        UPDATE feedback
        SET score = score + %s, num_reviews = num_reviews + 1
        WHERE comment_id = %s
        """,
        (score, comment_id)
    )
    conn.commit()
    conn.close()

# Start background task
async def startup_event():
    MAX_QUEUE_SIZE = 3
    for _ in range(MAX_QUEUE_SIZE):  # 3 parallel workers
        asyncio.create_task(process_review_queue())

@post("/")
async def trigger_review(request: Request) -> Response:
    try:
        data = await request.json()
        event_type = request.headers.get('X-Event-Key')
        if event_type in ('pr:opened', 'pr:from_ref_updated'):
            await review_queue.put({"json": data, "event_type": event_type})
            return Response("Your review request is queued and waiting for processing.", status_code=202)
        else:
            return Response("Payload received is not from a pull request. No code review process was triggered.", status_code=200)
    except Exception as e:
        return Response(f"Error: {str(e)}", status_code=500)

@get('/feedback', media_type=MediaType.HTML)
def feedback_endpoint(project: str, repo: str, pr_id: str, comment_id: str, score: int) -> str:
    load_dotenv()
    bitbucket_link = os.environ["BITBUCKET_LINK"] 
    pr_url = f"https://{bitbucket_link}/projects/{project}/repos/{repo}/pull-requests/{pr_id}"
    try:
        save_feedback(score, comment_id)
        status = "Feedback saved successfully"
    except Exception as e:
        console_logger.exception(f"Error occurred while saving feedback: {e}", pull_request=(project, repo, pr_id))
        file_logger.exception(f"Error occurred while saving feedback: {e}", pull_request=(project, repo, pr_id))
        status = "Error occurred while saving feedback"
    finally:
        html = f"""
        <!doctype html>
        <html>
            <head>
                <title>Redirect</title>
                <meta charset="utf-8" />
                <!-- Optional: meta refresh as a fallback if JS is disabled -->
                <meta http-equiv="refresh" content="2; URL='{pr_url}'" />
                <script type="text/javascript">
                    // Auto-redirect after 2 seconds
                    setTimeout(function() {{
                        window.location.href = "{pr_url}";
                    }}, 2000);
                </script>
            </head>
            <body>
                <p>{status}. You will be redirected to the pull request in a moment.</p>
                <p>If you are not redirected automatically, click the button below.</p>
                <button onclick="window.location.href='{pr_url}';">Redirect</button>
            </body>
        </html>
        """
        return html
        
        
app = Litestar(route_handlers=[trigger_review, feedback_endpoint], on_startup=[startup_event])

if __name__ == "__main__":
    uvicorn.run("webhook_receiver:app", host="0.0.0.0", port=5000)