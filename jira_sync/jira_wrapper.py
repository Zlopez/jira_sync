"""
Wrapper around the JIRA API.

See https://developer.atlassian.com/server/jira/platform/rest-apis/
"""
import logging

import jira

log = logging.getLogger()


class JIRA:
    """
    Class for interacting with JIRA API.

    Attributes:
      jira: Instance of the jira.JIRA object
      project: Project to work with
      issue_type: Default issue type for creating issues
      url_field_id: Field ID to use for storing original issue URL
      url_field_name: Field name to use for storing original issue URL
    """

    jira: jira.client.JIRA
    project: str
    issue_type: str

    def __init__(
            self,
            url: str,
            token: str,
            project: str,
            issue_type: str,
    ):
        """
        Class constructor.

        Params:
          url: URL to JIRA server
          token: Token to use for authentication
          project: Project to work with
          issue_type: Default issue type for creating issues
        """
        self.jira = jira.client.JIRA(url, token_auth=token)
        self.project = project
        self.issue_type = issue_type

    def get_issue_by_link(self, url: str) -> dict:
        """
        Return issue that has external issue URL set to url.

        Params:
          url: URL to search for

        Returns:
          Dictionary with retrieved issue or empty if nothing was retrieved.
        """
        issues = self.jira.search_issues(
            'project = ' + self.project + ' AND Description ~ \"' + url + '\"'
        )

        if not issues:
            return {}

        return issues

    def create_issue(
            self,
            summary: str,
            description: str,
            url: str,
            label: str = ""
    ) -> str:
        """
        Create new issue in JIRA.

        Params:
          summary: Name of the ticket
          description: Description of the ticket
          url: URL to add to url field, it is also added to description
          label: Label for the issue

        Returns:
          Id of the created issue or empty string.
        """
        issue_dict = {
            "project": {"key": self.project},
            "summary": summary,
            "description": url + "\n\n" + description,
            "issuetype": {"name": self.issue_type},
            "labels": [label] if label else [],
        }
        issue = self.jira.create_issue(fields=issue_dict)

        if issue:
            return issue.id

        return ""
