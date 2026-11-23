from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import Course, CourseDocument
from .services import answer_question, build_course_from_pdfs


def home(request):
    courses = Course.objects.order_by("-created_at")
    return render(request, "courses/home.html", {"courses": courses})


@require_POST
def create_course(request):
    title = (request.POST.get("title") or "Untitled course").strip()
    files = request.FILES.getlist("pdfs")
    pdfs = [f for f in files if f.name.lower().endswith(".pdf")]

    if not pdfs:
        courses = Course.objects.order_by("-created_at")
        return render(
            request,
            "courses/home.html",
            {"courses": courses, "error": "Please upload at least one PDF."},
            status=400,
        )

    course = Course.objects.create(title=title, status=Course.Status.PROCESSING)
    for pdf in pdfs:
        CourseDocument.objects.create(
            course=course, file=pdf, original_name=pdf.name
        )

    try:
        build_course_from_pdfs(course)
    except Exception:
        pass

    return redirect("course_detail", pk=course.pk)


def course_detail(request, pk):
    course = get_object_or_404(
        Course.objects.prefetch_related("modules__lessons", "documents"),
        pk=pk,
    )
    return render(request, "courses/course_detail.html", {"course": course})


@require_POST
def ask_rag(request, pk):
    course = get_object_or_404(Course, pk=pk)
    question = (request.POST.get("question") or "").strip()
    if not question:
        return JsonResponse({"error": "Enter a question."}, status=400)
    if course.status != Course.Status.READY:
        return JsonResponse(
            {"error": "The course is not ready for search yet."}, status=400
        )
    try:
        answer = answer_question(course, question)
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)
    return JsonResponse({"answer": answer})
