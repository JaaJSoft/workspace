"""Unfold admin theme configuration.

Icon names come from Material Symbols (https://fonts.google.com/icons), the
set unfold ships. The sidebar navigation lists the models an operator reaches
for day to day; the full per-app index stays available through the
"all applications" toggle at the bottom of the sidebar.
"""

from django.urls import reverse_lazy

UNFOLD = {
    "SITE_TITLE": "Workspace admin",
    "SITE_HEADER": "Workspace",
    "SITE_SUBHEADER": "Administration",
    "SITE_SYMBOL": "workspaces",
    "ENVIRONMENT": "workspace.core.services.admin_dashboard.environment_callback",
    "DASHBOARD_CALLBACK": "workspace.core.services.admin_dashboard.dashboard_callback",
    "COMMAND": {
        "search_models": True,
    },
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": True,
        "navigation": [
            {
                "title": "Users & access",
                "items": [
                    {
                        "title": "Users",
                        "icon": "person",
                        "link": reverse_lazy("admin:auth_user_changelist"),
                    },
                    {
                        "title": "Groups",
                        "icon": "group",
                        "link": reverse_lazy("admin:auth_group_changelist"),
                    },
                    {
                        "title": "Auth tokens",
                        "icon": "key",
                        "link": reverse_lazy("admin:knox_authtoken_changelist"),
                    },
                    {
                        "title": "User settings",
                        "icon": "tune",
                        "link": reverse_lazy("admin:users_usersetting_changelist"),
                    },
                ],
            },
            {
                "title": "Files",
                "separator": True,
                "items": [
                    {
                        "title": "Files",
                        "icon": "folder",
                        "link": reverse_lazy("admin:files_file_changelist"),
                    },
                    {
                        "title": "Shares",
                        "icon": "share",
                        "link": reverse_lazy("admin:files_fileshare_changelist"),
                    },
                    {
                        "title": "Comments",
                        "icon": "comment",
                        "link": reverse_lazy("admin:files_filecomment_changelist"),
                    },
                    {
                        "title": "Thumbnail failures",
                        "icon": "broken_image",
                        "link": reverse_lazy("admin:files_thumbnailfailure_changelist"),
                        "badge": "workspace.core.services.admin_dashboard.thumbnail_failure_badge",
                    },
                ],
            },
            {
                "title": "Chat",
                "separator": True,
                "items": [
                    {
                        "title": "Conversations",
                        "icon": "forum",
                        "link": reverse_lazy("admin:chat_conversation_changelist"),
                    },
                    {
                        "title": "Messages",
                        "icon": "chat",
                        "link": reverse_lazy("admin:chat_message_changelist"),
                    },
                    {
                        "title": "Attachments",
                        "icon": "attach_file",
                        "link": reverse_lazy("admin:chat_messageattachment_changelist"),
                    },
                ],
            },
            {
                "title": "Mail",
                "separator": True,
                "items": [
                    {
                        "title": "Accounts",
                        "icon": "alternate_email",
                        "link": reverse_lazy("admin:mail_mailaccount_changelist"),
                        "badge": "workspace.core.services.admin_dashboard.mail_sync_error_badge",
                    },
                    {
                        "title": "Messages",
                        "icon": "inbox",
                        "link": reverse_lazy("admin:mail_mailmessage_changelist"),
                    },
                    {
                        "title": "Folders",
                        "icon": "folder_open",
                        "link": reverse_lazy("admin:mail_mailfolder_changelist"),
                    },
                    {
                        "title": "Rules",
                        "icon": "rule",
                        "link": reverse_lazy("admin:mail_mailrule_changelist"),
                    },
                ],
            },
            {
                "title": "Calendar",
                "separator": True,
                "items": [
                    {
                        "title": "Calendars",
                        "icon": "calendar_month",
                        "link": reverse_lazy("admin:calendar_calendar_changelist"),
                    },
                    {
                        "title": "Events",
                        "icon": "event",
                        "link": reverse_lazy("admin:calendar_event_changelist"),
                    },
                    {
                        "title": "External calendars",
                        "icon": "cloud_sync",
                        "link": reverse_lazy(
                            "admin:calendar_externalcalendar_changelist"
                        ),
                        "badge": "workspace.core.services.admin_dashboard.external_calendar_error_badge",
                    },
                    {
                        "title": "Polls",
                        "icon": "ballot",
                        "link": reverse_lazy("admin:calendar_poll_changelist"),
                    },
                ],
            },
            {
                "title": "AI",
                "separator": True,
                "items": [
                    {
                        "title": "Tasks",
                        "icon": "neurology",
                        "link": reverse_lazy("admin:ai_aitask_changelist"),
                        "badge": "workspace.core.services.admin_dashboard.failed_ai_task_badge",
                    },
                    {
                        "title": "Bot profiles",
                        "icon": "smart_toy",
                        "link": reverse_lazy("admin:ai_botprofile_changelist"),
                    },
                    {
                        "title": "Conversation summaries",
                        "icon": "summarize",
                        "link": reverse_lazy("admin:ai_conversationsummary_changelist"),
                    },
                ],
            },
            {
                "title": "Projects",
                "separator": True,
                "items": [
                    {
                        "title": "Projects",
                        "icon": "view_kanban",
                        "link": reverse_lazy("admin:projects_project_changelist"),
                    },
                    {
                        "title": "Tasks",
                        "icon": "task_alt",
                        "link": reverse_lazy("admin:projects_task_changelist"),
                    },
                ],
            },
            {
                "title": "Imports",
                "separator": True,
                "items": [
                    {
                        "title": "Connections",
                        "icon": "cloud_download",
                        "link": reverse_lazy(
                            "admin:imports_importconnection_changelist"
                        ),
                    },
                    {
                        "title": "Jobs",
                        "icon": "sync",
                        "link": reverse_lazy("admin:imports_importjob_changelist"),
                        "badge": "workspace.core.services.admin_dashboard.failed_import_job_badge",
                    },
                ],
            },
            {
                "title": "Vault",
                "separator": True,
                "items": [
                    {
                        "title": "Vaults",
                        "icon": "lock",
                        "link": reverse_lazy("admin:vault_vault_changelist"),
                    },
                    {
                        "title": "Entries",
                        "icon": "password",
                        "link": reverse_lazy("admin:vault_vaultentry_changelist"),
                    },
                    {
                        "title": "Identities",
                        "icon": "fingerprint",
                        "link": reverse_lazy("admin:vault_accountidentity_changelist"),
                    },
                ],
            },
            {
                "title": "Notifications",
                "separator": True,
                "items": [
                    {
                        "title": "Notifications",
                        "icon": "notifications",
                        "link": reverse_lazy(
                            "admin:notifications_notification_changelist"
                        ),
                    },
                    {
                        "title": "Push subscriptions",
                        "icon": "notifications_active",
                        "link": reverse_lazy(
                            "admin:notifications_pushsubscription_changelist"
                        ),
                    },
                ],
            },
        ],
    },
}
