from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import serializers

from workspace.common.fields import CaseInsensitiveSlugRelatedField

User = get_user_model()


class _UserSerializer(serializers.Serializer):
    user = CaseInsensitiveSlugRelatedField(
        slug_field='username',
        queryset=User.objects.all(),
    )


class CaseInsensitiveSlugRelatedFieldTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username='alice', password='pass')
        cls.bob = User.objects.create_user(username='Bob', password='pass')

    def test_resolves_exact_match(self):
        serializer = _UserSerializer(data={'user': 'alice'})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data['user'], self.alice)

    def test_resolves_different_case(self):
        serializer = _UserSerializer(data={'user': 'ALICE'})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data['user'], self.alice)

    def test_resolves_mixed_case_stored_value(self):
        serializer = _UserSerializer(data={'user': 'bob'})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data['user'], self.bob)

    def test_unknown_value_raises_does_not_exist(self):
        serializer = _UserSerializer(data={'user': 'nobody'})
        self.assertFalse(serializer.is_valid())
        self.assertIn('user', serializer.errors)
        # DRF uses the "does_not_exist" message for unknown slugs.
        self.assertIn('does not exist', str(serializer.errors['user']).lower())

    def test_requires_queryset(self):
        field = CaseInsensitiveSlugRelatedField(slug_field='username', read_only=True)
        # Bypass the read_only guard that blocks to_internal_value in DRF —
        # we want to exercise the explicit assertion inside the method.
        field.read_only = False
        with self.assertRaises(AssertionError):
            field.to_internal_value('alice')

    def test_ambiguous_match_returns_invalid(self):
        # Django's default User.username has unique=True but the index is
        # case-sensitive, so 'Dave' and 'dave' can coexist. A case-insensitive
        # lookup then matches both rows and MultipleObjectsReturned is raised.
        User.objects.create_user(username='Dave', password='pass')
        User.objects.create_user(username='dave', password='pass')

        serializer = _UserSerializer(data={'user': 'DAVE'})
        self.assertFalse(serializer.is_valid())
        self.assertIn('user', serializer.errors)
        # The "invalid" error key is used for the ambiguous case.
        self.assertIn('invalid', str(serializer.errors['user']).lower())
