from django.urls import path

from . import (
    views,
    views_attachments,
    views_compose,
    views_contacts,
    views_folders,
    views_labels,
    views_messages,
    views_oauth2,
)

urlpatterns = [
    path('api/v1/mail/autodiscover', views.MailAutodiscoverView.as_view(), name='mail-autodiscover'),
    path('api/v1/mail/accounts', views.MailAccountListView.as_view(), name='mail-account-list'),
    path('api/v1/mail/accounts/<uuid:uuid>', views.MailAccountDetailView.as_view(), name='mail-account-detail'),
    path('api/v1/mail/accounts/<uuid:uuid>/test', views.MailAccountTestView.as_view(), name='mail-account-test'),
    path('api/v1/mail/accounts/<uuid:uuid>/sync', views.MailAccountSyncView.as_view(), name='mail-account-sync'),
    path('api/v1/mail/labels', views_labels.MailLabelListView.as_view(), name='mail-label-list'),
    path('api/v1/mail/labels/<uuid:uuid>', views_labels.MailLabelDetailView.as_view(), name='mail-label-detail'),
    path('api/v1/mail/folders', views_folders.MailFolderListView.as_view(), name='mail-folder-list'),
    path('api/v1/mail/folders/<uuid:uuid>', views_folders.MailFolderUpdateView.as_view(), name='mail-folder-update'),
    path('api/v1/mail/folders/<uuid:uuid>/mark-read', views_folders.MailFolderMarkReadView.as_view(), name='mail-folder-mark-read'),
    path('api/v1/mail/contacts/autocomplete', views_contacts.ContactAutocompleteView.as_view(), name='mail-contact-autocomplete'),
    path('api/v1/mail/messages', views_messages.MailMessageListView.as_view(), name='mail-message-list'),
    path('api/v1/mail/drafts', views_compose.MailDraftView.as_view(), name='mail-draft'),
    path('api/v1/mail/drafts/<uuid:uuid>', views_compose.MailDraftView.as_view(), name='mail-draft-detail'),
    path('api/v1/mail/messages/send', views_compose.MailSendView.as_view(), name='mail-send'),
    path('api/v1/mail/messages/batch-action', views_messages.MailBatchActionView.as_view(), name='mail-batch-action'),
    path('api/v1/mail/messages/<uuid:uuid>/labels', views_labels.MailMessageLabelView.as_view(), name='mail-message-labels'),
    path('api/v1/mail/messages/<uuid:uuid>', views_messages.MailMessageDetailView.as_view(), name='mail-message-detail'),
    path('api/v1/mail/attachments/<uuid:uuid>', views_attachments.MailAttachmentDownloadView.as_view(), name='mail-attachment-download'),
    path('api/v1/mail/attachments/<uuid:uuid>/save-to-files', views_attachments.MailAttachmentSaveToFilesView.as_view(), name='mail-attachment-save-to-files'),
    path('api/v1/mail/oauth2/providers', views_oauth2.OAuthProvidersView.as_view(), name='mail-oauth2-providers'),
    path('api/v1/mail/oauth2/authorize', views_oauth2.OAuthAuthorizeView.as_view(), name='mail-oauth2-authorize'),
]
