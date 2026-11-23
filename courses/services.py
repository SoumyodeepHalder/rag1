import json
import os
import re
from pathlib import Path

from django.conf import settings
from langchain_community.document_loaders import PyPDFLoader
from langchain_groq import ChatGroq
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .models import Course, CourseModule, Lesson
from .vectorstore import VectorStore


def get_llm():
    groq_api_key = settings.GROQ_API_KEY
    llm_model = settings.LLM_MODEL
    return ChatGroq(groq_api_key=groq_api_key, model_name=llm_model)


def build_course_from_pdfs(course: Course):
    persist_dir = os.path.join(settings.VECTORSTORE_ROOT, str(course.pk))
    os.makedirs(persist_dir, exist_ok=True)
    course.persist_dir = persist_dir
    course.status = Course.Status.PROCESSING
    course.error_message = ""
    course.save(update_fields=["persist_dir", "status", "error_message"])

    try:
        documents = []
        for doc in course.documents.all():
            loader = PyPDFLoader(doc.file.path)
            pages = loader.load()
            for page in pages:
                page.metadata["source"] = doc.original_name
            documents.extend(pages)

        if not documents:
            raise ValueError("No text could be extracted from the uploaded PDFs.")

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=150,
            separators=["\n\n", "\n", ". ", " ", ""],
        )
        chunks = splitter.split_documents(documents)
        texts = [chunk.page_content.strip() for chunk in chunks if chunk.page_content.strip()]
        metadatas = [chunk.metadata for chunk in chunks if chunk.page_content.strip()]

        store = VectorStore(persist_dir, embedding_model=settings.EMBEDDING_MODEL)
        store.build(texts, metadatas)

        outline = generate_course_outline(course, texts)
        print(outline)
        persist_outline(course, outline)
        course.status = Course.Status.READY
        course.save(update_fields=["title", "description", "learning_objectives", "status"])
    except Exception as exc:
        course.status = Course.Status.FAILED
        course.error_message = str(exc)
        course.save(update_fields=["status", "error_message"])
        raise


def generate_course_outline(course: Course, texts):
    sample = "\n\n".join(texts[:12])[:8000]
    llm = get_llm()
    prompt = f"""You are an instructional designer. Create a SHORT student-centric course from the source material.

The course must have ONLY 3 to 5 pages of explanation. Each page is a brief teaching note, not a textbook chapter.

Return ONLY valid JSON with this shape:
{{
  "title": "short course title",
  "description": "1-2 sentence overview",
  "learning_objectives": ["objective 1", "objective 2", "objective 3"],
  "pages": [
    {{
      "title": "page title",
      "content": "one paragraph, 60-90 words, explaining a single idea"
    }}
  ]
}}

Rules:
- Exactly 3, 4, or 5 pages. Never more.
- Each page: one idea, one paragraph, no lists of sub-lessons.
- Keep the whole course readable in a few minutes.
- Ground every page in the source material.

Working title from the user: {course.title}

SOURCE MATERIAL:
{sample}
"""
    response = llm.invoke(prompt)
    content = response.content if hasattr(response, "content") else str(response)
    return parse_json_response(content)


def persist_outline(course: Course, outline: dict):
    title = (outline.get("title") or course.title or "Untitled course").strip()
    course.title = title
    course.description = outline.get("description", "")
    course.learning_objectives = list(outline.get("learning_objectives", []))[:3]
    course.modules.all().delete()

    pages = outline.get("pages") or []
    if not pages:
        for module_data in outline.get("modules", []):
            for lesson_data in module_data.get("lessons", []) or [
                {"title": module_data.get("title"), "content": module_data.get("summary", "")}
            ]:
                pages.append(lesson_data)

    pages = pages[:5]
    module = CourseModule.objects.create(
        course=course,
        title="Course pages",
        summary="A short set of explanation pages.",
        order=1,
    )
    for page_index, page_data in enumerate(pages, start=1):
        content = (page_data.get("content") or "").strip()
        words = content.split()
        if len(words) > 120:
            content = " ".join(words[:120])
        Lesson.objects.create(
            module=module,
            title=page_data.get("title", f"Page {page_index}"),
            content=content,
            order=page_index,
        )


def answer_question(course: Course, question: str):
    if not course.persist_dir or not Path(course.persist_dir).exists():
        raise ValueError("This course has no vector index yet.")

    store = VectorStore(course.persist_dir, embedding_model=settings.EMBEDDING_MODEL)
    hits = store.search(question, k=5)
    if not hits:
        return "I could not find matching material in the uploaded PDFs."

    context = "\n\n".join(
        f"[{hit['metadata'].get('source', 'PDF')} p.{hit['metadata'].get('page', '?')}]\n{hit['text']}"
        for hit in hits
    )
    llm = get_llm()
    prompt = f"""Answer the student's question using only the retrieved course material.
If the answer is not in the context, say you do not have enough information.
Be clear and teaching-oriented.

QUESTION:
{question}

CONTEXT:
{context}
"""
    response = llm.invoke(prompt)
    return response.content if hasattr(response, "content") else str(response)


def parse_json_response(text: str):
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            return json.loads(text[start : end + 1])
        raise ValueError("The model did not return valid course JSON.")
