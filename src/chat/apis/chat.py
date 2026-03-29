from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from chat.apis.gemini_service import GeminiService
from chat.serializers import (
    ChatMessageSerializer,
    ChatResponseSerializer,
    ChatWithHistorySerializer,
    ChatResponseWithHistorySerializer,
)


@extend_schema(
    summary="Chat with Gemini (Client)",
    request=ChatMessageSerializer,
    responses={200: ChatResponseSerializer},
    tags=["Chat - Client"],
)
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def client_chat(request):
    """
    Client-side chat endpoint with Gemini.
    Gemini acts as a personal fitness coach and answers fitness-related questions.
    """
    serializer = ChatMessageSerializer(data=request.data)
    
    if not serializer.is_valid():
        return Response(
            {"status": False, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        gemini = GeminiService()
        message = serializer.validated_data['message']
        response = gemini.generate_client_response(message)
        
        return Response({
            "response": response,
            "status": True,
        }, status=status.HTTP_200_OK)
    
    except Exception as e:
        return Response({
            "response": None,
            "status": False,
            "error": str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@extend_schema(
    summary="Chat with Gemini (Client) - With History",
    request=ChatWithHistorySerializer,
    responses={200: ChatResponseWithHistorySerializer},
    tags=["Chat - Client"],
)
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def client_chat_with_history(request):
    """
    Client-side chat with conversation history for better context.
    """
    serializer = ChatWithHistorySerializer(data=request.data)
    
    if not serializer.is_valid():
        return Response(
            {"status": False, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        gemini = GeminiService()
        message = serializer.validated_data['message']
        history = serializer.validated_data.get('conversation_history', [])
        
        response = gemini.generate_client_response(message, history)
        
        return Response({
            "response": response,
            "message": message,
            "status": True,
        }, status=status.HTTP_200_OK)
    
    except Exception as e:
        return Response({
            "response": None,
            "status": False,
            "error": str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@extend_schema(
    summary="Chat with Gemini (Trainer)",
    request=ChatMessageSerializer,
    responses={200: ChatResponseSerializer},
    tags=["Chat - Trainer"],
)
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def trainer_chat(request):
    """
    Trainer-side chat endpoint with Gemini.
    Gemini acts as a co-trainer assistant providing fitness advice.
    """
    serializer = ChatMessageSerializer(data=request.data)
    
    if not serializer.is_valid():
        return Response(
            {"status": False, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        gemini = GeminiService()
        message = serializer.validated_data['message']
        response = gemini.generate_trainer_response(message)
        
        return Response({
            "response": response,
            "status": True,
        }, status=status.HTTP_200_OK)
    
    except Exception as e:
        return Response({
            "response": None,
            "status": False,
            "error": str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@extend_schema(
    summary="Chat with Gemini (Trainer) - With History",
    request=ChatWithHistorySerializer,
    responses={200: ChatResponseWithHistorySerializer},
    tags=["Chat - Trainer"],
)
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def trainer_chat_with_history(request):
    """
    Trainer-side chat with conversation history for better context.
    """
    serializer = ChatWithHistorySerializer(data=request.data)
    
    if not serializer.is_valid():
        return Response(
            {"status": False, "errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        gemini = GeminiService()
        message = serializer.validated_data['message']
        history = serializer.validated_data.get('conversation_history', [])
        
        response = gemini.generate_trainer_response(message, history)
        
        return Response({
            "response": response,
            "message": message,
            "status": True,
        }, status=status.HTTP_200_OK)
    
    except Exception as e:
        return Response({
            "response": None,
            "status": False,
            "error": str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
