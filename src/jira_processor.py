from dotenv import load_dotenv
from requests.exceptions import HTTPError
import os
import requests
import json
from typing_extensions import Tuple, List

class JIRAProcessor:
    def __init__(self) -> None:
        load_dotenv()
        self.username = os.environ["JIRA_USERNAME"]
        self.password = os.environ["JIRA_PASSWORD"]
        self.jira_link = os.environ["JIRA_LINK"]
    
    def get_issue(self, issue_key: str) -> Tuple[str, str]:
        url = f"http://{self.jira_link}/rest/agile/1.0/issue/{issue_key}"
        auth = (self.username, self.password)

        headers = {
            "Accept": "application/json"
        }

        response = requests.request("GET", url, auth=auth, headers=headers)
        response.raise_for_status()
        response_json = json.loads(response.text)
        issue_summary = response_json['fields']['summary']
        issue_description = response_json['fields']['description']
        return (issue_summary, issue_description)
    
    def get_confluence_links(self, issue_key: str) -> List[str]:
        try:
            url = f"http://{self.jira_link}/rest/api/2/issue/{issue_key}/remotelink"
            auth = (self.username, self.password)

            headers = {
            "Accept": "application/json"
            }

            response = requests.request("GET", url, headers=headers, auth=auth)
            response.raise_for_status()
            link_list = json.loads(response.text)
            confluence_links = [l for l in link_list if 'confluence' in l]
            return confluence_links
        except Exception:
            return []