from django.db import migrations


def populate_unread_counts(apps, schema_editor):
    ConversationMember = apps.get_model('chat', 'ConversationMember')
    Message = apps.get_model('chat', 'Message')

    members = ConversationMember.objects.filter(
        left_at__isnull=True,
    ).iterator(chunk_size=500)

    for member in members:
        qs = Message.objects.filter(
            conversation_id=member.conversation_id,
            deleted_at__isnull=True,
        ).exclude(author_id=member.user_id)

        if member.last_read_at is not None:
            qs = qs.filter(created_at__gt=member.last_read_at)

        count = qs.count()
        if count > 0:
            ConversationMember.objects.filter(pk=member.pk).update(
                unread_count=count,
            )


class Migration(migrations.Migration):

    dependencies = [
        ('chat', '0008_conversationmember_unread_count'),
    ]

    operations = [
        migrations.RunPython(
            populate_unread_counts,
            migrations.RunPython.noop,
        ),
    ]
