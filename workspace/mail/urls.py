from django.urls import path

from . import views

urlpatterns = [
    path('api/v1/mail/accounts', views.MailAccountListView.as_view(), name='mail-account-list'),
    path('api/v1/mail/accounts/<uuid:uuid>', views.MailAccountDetailView.as_view(), name='mail-account-detail'),
    path('api/v1/mail/accounts/<uuid:uuid>/test', views.MailAccountTestView.as_view(), name='mail-account-test'),
    path('api/v1/mail/accounts/<uuid:uuid>/sync', views.MailAccountSyncView.as_view(), name='mail-account-sync'),
    path('api/v1/mail/folders', views.MailFolderListView.as_view(), name='mail-folder-list'),
    path('api/v1/mail/messages', views.MailMessageListView.as_view(), name='mail-message-list'),
    path('api/v1/mail/drafts', views.MailDraftView.as_view(), name='mail-draft'),
    path('api/v1/mail/drafts/<uuid:uuid>', views.MailDraftView.as_view(), name='mail-draft-detail'),
    path('api/v1/mail/messages/send', views.MailSendView.as_view(), name='mail-send'),
    path('api/v1/mail/messages/batch-action', views.MailBatchActionView.as_view(), name='mail-batch-action'),
    path('api/v1/mail/messages/<uuid:uuid>', views.MailMessageDetailView.as_view(), name='mail-message-detail'),
    path('api/v1/mail/attachments/<uuid:uuid>', views.MailAttachmentDownloadView.as_view(), name='mail-attachment-download'),
]
