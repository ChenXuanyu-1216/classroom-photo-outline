"""Owner-scoped EXP05 evidence. Capture/review/export never runs the model."""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import EvaluationSnapshot, EvaluationReview
from app.services.auth_service import require_task_owner
from app.services.task_execution import task_edit_lock, task_data_lock
from app.services.task_jobs import reject_active_job
from app.services.evaluation import capture, get_snapshot, summary, detail, review_view, ReviewInput, validate_module_binding
from uuid import uuid4

router = APIRouter(dependencies=[Depends(require_task_owner)])


@router.get('/api/tasks/{task_id}/evaluations')
def list_evaluations(task_id: int, db: Session = Depends(get_db)):
    rows = db.query(EvaluationSnapshot).filter_by(task_id=task_id).order_by(EvaluationSnapshot.created_at.desc()).all()
    return {'snapshots': [summary(row) for row in rows]}


@router.post('/api/tasks/{task_id}/evaluations', status_code=201)
def capture_current(task_id: int, db: Session = Depends(get_db)):
    with task_edit_lock(db, task_id):
        reject_active_job(db, task_id)
        snapshot = capture(db, task_id)
        db.commit()
        return summary(snapshot)


@router.get('/api/tasks/{task_id}/evaluations/{snapshot_id}')
def get_evaluation(task_id: int, snapshot_id: str, db: Session = Depends(get_db)):
    return detail(get_snapshot(db, task_id, snapshot_id))


@router.post('/api/tasks/{task_id}/evaluations/{snapshot_id}/reviews', status_code=201)
def save_review(task_id: int, snapshot_id: str, body: ReviewInput, db: Session = Depends(get_db)):
    with task_data_lock(db, task_id):
        snapshot = get_snapshot(db, task_id, snapshot_id)
        validate_module_binding(snapshot, body)
        review = EvaluationReview(id=str(uuid4()), snapshot_id=snapshot.id, body=body.model_dump())
        db.add(review)
        db.commit()
        return review_view(review, snapshot)


@router.get('/api/tasks/{task_id}/evaluations/{snapshot_id}/export')
def export_evaluation(task_id: int, snapshot_id: str, db: Session = Depends(get_db)):
    result = detail(get_snapshot(db, task_id, snapshot_id))
    return JSONResponse(result, headers={'Content-Disposition': f'attachment; filename="exp05-{snapshot_id}.json"',
                                         'Cache-Control': 'no-store'})
