from rest_framework import serializers

from .models import Tag


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ['uuid', 'name', 'color', 'created_at']
        read_only_fields = ['uuid', 'created_at']

    def validate_name(self, value):
        user = self.context['request'].user
        qs = Tag.objects.filter(owner=user, name=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError('A tag with this name already exists.')
        return value

    def create(self, validated_data):
        validated_data['owner'] = self.context['request'].user
        return super().create(validated_data)
