"""E2E: the task panel opens its attachments and linked files in the viewer.

A task attachment is a copy the task owns, rendered by the projects viewer
endpoint and downloaded from the task. A linked file stays a Files file,
rendered and downloaded through Files. Both open in the same modal, from
the task panel's lists.

Skipped unless E2E=1 is set.
"""

from __future__ import annotations

import base64

from django.core.files.uploadedfile import SimpleUploadedFile
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.services import FileService
from workspace.projects.services.attachments import create_attachments
from workspace.projects.services.file_links import link_files
from workspace.projects.services.projects import create_project
from workspace.projects.services.tasks import create_task

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


class TaskAttachmentViewerTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="alice")
        self.project = create_project(self.user, name="Website")
        self.task = create_task(self.project, self.user, title="Write the brief")
        (self.attachment,) = create_attachments(
            self.user,
            self.task,
            [SimpleUploadedFile("mockup.png", PNG_1PX, content_type="image/png")],
            [],
        )
        self.linked = FileService.create_file(
            self.user,
            "logo.png",
            content=SimpleUploadedFile("logo.png", PNG_1PX, content_type="image/png"),
        )
        link_files(self.user, self.task, [self.linked])
        self.base = f"{self.live_server_url}/projects/{self.project.uuid}"
        self.login_as(self.user)

        self.page.goto(f"{self.base}/backlog?task={self.task.uuid}")
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        expect(self.page.locator("#task-panel")).to_contain_text("Write the brief")
        self.viewer = self.page.locator("dialog[open]")

    def _close(self):
        self.viewer.locator('button[title="Close (ESC)"]').click()
        expect(self.viewer).to_have_count(0)

    def test_a_task_attachment_opens_from_the_task(self):
        self.page.locator("#task-panel").get_by_role(
            "button", name="mockup.png"
        ).click()

        expect(self.viewer.locator("h3")).to_have_text("mockup.png")
        expect(self.viewer.locator("#viewer-panel img")).to_be_visible()
        expect(self.viewer.locator("a[download]")).to_have_attribute(
            "href",
            f"/api/v1/projects/{self.project.uuid}/tasks/{self.task.uuid}"
            f"/attachments/{self.attachment.uuid}/download",
        )
        self._close()
        self.assertEqual(
            self.page.locator("#viewer-panel").evaluate("el => el.childElementCount"),
            0,
        )

    def test_a_linked_file_opens_through_files(self):
        self.page.locator("#task-panel").get_by_role("button", name="logo.png").click()

        expect(self.viewer.locator("h3")).to_have_text("logo.png")
        expect(
            self.viewer.locator(f'#viewer-panel img[src*="{self.linked.uuid}"]')
        ).to_be_visible()
        expect(self.viewer.locator("a[download]")).to_have_attribute(
            "href", f"/api/v1/files/{self.linked.uuid}/download"
        )
        self._close()

    def test_back_closes_the_viewer_and_leaves_the_task_open(self):
        task_url = f"{self.base}/backlog?task={self.task.uuid}"
        self.page.locator("#task-panel").get_by_role(
            "button", name="mockup.png"
        ).click()
        expect(self.viewer.locator("h3")).to_have_text("mockup.png")
        expect(self.page).to_have_url(f"{task_url}&open={self.attachment.uuid}")

        self.page.go_back()

        expect(self.viewer).to_have_count(0)
        expect(self.page).to_have_url(task_url)
        expect(self.page.locator("#task-panel")).to_contain_text("Write the brief")

        self.page.go_forward()
        expect(self.viewer.locator("h3")).to_have_text("mockup.png")
        expect(self.viewer.locator("#viewer-panel img")).to_be_visible()
        self._close()
        expect(self.page).to_have_url(task_url)

    def test_only_a_linked_file_gets_the_favorite_star(self):
        star = self.viewer.locator("[data-viewer-favorite]")
        self.page.locator("#task-panel").get_by_role(
            "button", name="mockup.png"
        ).click()
        expect(self.viewer.locator("#viewer-panel img")).to_be_visible()
        expect(star).to_be_hidden()
        self._close()

        self.page.locator("#task-panel").get_by_role("button", name="logo.png").click()
        expect(star).to_be_enabled()
        star.click()
        expect(star).to_have_attribute("aria-pressed", "true")
