from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.models import File, FileComment, FileShare
from workspace.files.services.comments import mentionable_users
from workspace.notifications.models import Notification

User = get_user_model()


class FileCommentMentionTestBase(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner", email="owner@example.com", password="pass123"
        )
        self.shared = User.objects.create_user(
            username="shared", email="shared@example.com", password="pass123"
        )
        self.outsider = User.objects.create_user(
            username="outsider", email="outsider@example.com", password="pass123"
        )
        self.file = File.objects.create(
            owner=self.owner,
            name="doc.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
        )
        FileShare.objects.create(
            file=self.file, shared_by=self.owner, shared_with=self.shared
        )
        self.client.force_authenticate(user=self.owner)

    def _notifs_for(self, user):
        return Notification.objects.filter(recipient=user, origin="files")

    def _post_comment(self, body):
        return self.client.post(
            f"/api/v1/files/{self.file.uuid}/comments", {"body": body}, format="json"
        )


class MentionableUsersTests(FileCommentMentionTestBase):
    def test_owner_and_shared_users_are_mentionable(self):
        users = mentionable_users(self.file)
        self.assertEqual({u.pk for u in users}, {self.owner.pk, self.shared.pk})

    def test_group_members_are_mentionable(self):
        group = Group.objects.create(name="team")
        member = User.objects.create_user(
            username="member", email="member@example.com", password="pass123"
        )
        member.groups.add(group)
        group_file = File.objects.create(
            owner=self.owner,
            name="group.txt",
            node_type=File.NodeType.FILE,
            group=group,
        )
        users = mentionable_users(group_file)
        self.assertIn(member.pk, {u.pk for u in users})

    def test_inactive_users_are_not_mentionable(self):
        self.shared.is_active = False
        self.shared.save(update_fields=["is_active"])
        self.assertEqual({u.pk for u in mentionable_users(self.file)}, {self.owner.pk})
        self.owner.is_active = False
        self.owner.save(update_fields=["is_active"])
        self.file.refresh_from_db()
        self.assertEqual(mentionable_users(self.file), [])

    def test_sorted_by_username(self):
        users = mentionable_users(self.file)
        self.assertEqual(
            [u.username for u in users],
            sorted((u.username for u in users), key=str.lower),
        )


class CommentMentionNotificationTests(FileCommentMentionTestBase):
    def test_mentioned_audience_user_gets_high_priority_notification(self):
        resp = self._post_comment("ping @shared please review")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        notifs = self._notifs_for(self.shared)
        self.assertEqual(notifs.count(), 1)
        n = notifs.first()
        self.assertIn("mentioned", n.title)
        self.assertIn("doc.txt", n.title)
        self.assertEqual(n.priority, "high")
        self.assertEqual(n.actor, self.owner)

    def test_mentioned_user_not_doubly_notified(self):
        """A prior commenter who is also mentioned gets only the mention notif."""
        FileComment.objects.create(file=self.file, author=self.shared, body="First!")
        self._post_comment("thanks @shared")
        self.assertEqual(self._notifs_for(self.shared).count(), 1)
        self.assertIn("mentioned", self._notifs_for(self.shared).first().title)

    def test_mention_outside_audience_is_ignored(self):
        self._post_comment("hey @outsider look at this")
        self.assertEqual(self._notifs_for(self.outsider).count(), 0)

    def test_unknown_username_is_ignored(self):
        resp = self._post_comment("hello @nosuchuser")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Notification.objects.count(), 0)

    def test_self_mention_no_notification(self):
        self._post_comment("note to @owner myself")
        self.assertEqual(self._notifs_for(self.owner).count(), 0)

    def test_regular_recipients_still_notified(self):
        """Owner keeps the plain 'commented' notif when someone else is mentioned."""
        third = User.objects.create_user(
            username="third", email="third@example.com", password="pass123"
        )
        FileShare.objects.create(
            file=self.file, shared_by=self.owner, shared_with=third
        )
        self.client.force_authenticate(user=self.shared)
        self._post_comment("cc @third")
        self.assertEqual(self._notifs_for(third).count(), 1)
        self.assertIn("mentioned", self._notifs_for(third).first().title)
        owner_notifs = self._notifs_for(self.owner)
        self.assertEqual(owner_notifs.count(), 1)
        self.assertIn("commented", owner_notifs.first().title)

    def test_edit_notifies_newly_mentioned_only(self):
        resp = self._post_comment("draft note")
        comment_uuid = resp.json()["uuid"]
        resp = self.client.patch(
            f"/api/v1/files/{self.file.uuid}/comments/{comment_uuid}",
            {"body": "draft note, ping @shared"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        notifs = self._notifs_for(self.shared)
        self.assertEqual(notifs.count(), 1)
        self.assertIn("mentioned", notifs.first().title)

    def test_edit_does_not_renotify_existing_mention(self):
        resp = self._post_comment("ping @shared")
        comment_uuid = resp.json()["uuid"]
        self.client.patch(
            f"/api/v1/files/{self.file.uuid}/comments/{comment_uuid}",
            {"body": "ping @shared again"},
            format="json",
        )
        self.assertEqual(self._notifs_for(self.shared).count(), 1)


class DottedUsernameMentionTests(FileCommentMentionTestBase):
    """Usernames like 'jean.dupont' are the common shape; they must mention."""

    def setUp(self):
        super().setUp()
        self.dotted = User.objects.create_user(
            username="jean.dupont", email="jd@example.com", password="pass123"
        )
        FileShare.objects.create(
            file=self.file, shared_by=self.owner, shared_with=self.dotted
        )

    def test_dotted_username_gets_notified(self):
        resp = self._post_comment("ping @jean.dupont please review")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        notifs = self._notifs_for(self.dotted)
        self.assertEqual(notifs.count(), 1)
        self.assertIn("mentioned", notifs.first().title)
        self.assertEqual(notifs.first().priority, "high")

    def test_dotted_username_renders_as_badge(self):
        resp = self._post_comment("ping @jean.dupont")
        body_html = resp.json()["body_html"]
        self.assertIn(f'data-user-id="{self.dotted.pk}"', body_html)
        self.assertIn(">@jean.dupont</span>", body_html)

    def test_edit_notifies_newly_mentioned_dotted_username(self):
        resp = self._post_comment("draft note")
        comment_uuid = resp.json()["uuid"]
        self.client.patch(
            f"/api/v1/files/{self.file.uuid}/comments/{comment_uuid}",
            {"body": "draft note, ping @jean.dupont"},
            format="json",
        )
        self.assertEqual(self._notifs_for(self.dotted).count(), 1)

    def test_edit_does_not_renotify_existing_dotted_mention(self):
        resp = self._post_comment("ping @jean.dupont")
        comment_uuid = resp.json()["uuid"]
        self.client.patch(
            f"/api/v1/files/{self.file.uuid}/comments/{comment_uuid}",
            {"body": "ping @jean.dupont again"},
            format="json",
        )
        self.assertEqual(self._notifs_for(self.dotted).count(), 1)

    def test_shorter_prefix_user_is_not_notified(self):
        """@jean.dupont must not also ping a user named 'jean'."""
        jean = User.objects.create_user(
            username="jean", email="jean@example.com", password="pass123"
        )
        FileShare.objects.create(file=self.file, shared_by=self.owner, shared_with=jean)
        self._post_comment("ping @jean.dupont")
        self.assertEqual(self._notifs_for(jean).count(), 0)
        self.assertEqual(self._notifs_for(self.dotted).count(), 1)


class CommentListShapeTests(FileCommentMentionTestBase):
    def test_list_returns_comments_and_mention_users(self):
        FileComment.objects.create(file=self.file, author=self.owner, body="hi")
        resp = self.client.get(f"/api/v1/files/{self.file.uuid}/comments")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        self.assertEqual(len(data["comments"]), 1)
        self.assertEqual(
            {u["username"] for u in data["mention_users"]}, {"owner", "shared"}
        )
        self.assertEqual(
            set(data["mention_users"][0].keys()),
            {"id", "username", "first_name", "last_name"},
        )

    def test_body_html_renders_mention_badge(self):
        FileComment.objects.create(
            file=self.file, author=self.owner, body="ping @shared"
        )
        resp = self.client.get(f"/api/v1/files/{self.file.uuid}/comments")
        body_html = resp.json()["comments"][0]["body_html"]
        self.assertIn("mention-badge", body_html)
        self.assertIn(f'data-user-id="{self.shared.pk}"', body_html)

    def test_body_html_escapes_html(self):
        FileComment.objects.create(
            file=self.file, author=self.owner, body="<script>x</script>"
        )
        resp = self.client.get(f"/api/v1/files/{self.file.uuid}/comments")
        body_html = resp.json()["comments"][0]["body_html"]
        self.assertNotIn("<script>", body_html)

    def test_create_response_includes_body_html(self):
        resp = self._post_comment("hello @shared")
        self.assertIn("mention-badge", resp.json()["body_html"])
