from rest_framework import serializers


class CallSignalSerializer(serializers.Serializer):
    to_user = serializers.IntegerField()
    type = serializers.ChoiceField(choices=['offer', 'answer', 'ice'])
    payload = serializers.DictField()


class CallMuteSerializer(serializers.Serializer):
    muted = serializers.BooleanField()
