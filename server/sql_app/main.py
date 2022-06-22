from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from . import crud, models, schemas
from .database import SessionLocal, engine

# Create the db tables
models.Base.metadata.create_all(bind=engine)

# Normally you would probably initialize your db (create tables, etc) with
# Alembic. Would also use Alembic for "migrations" (that's its main job).
# A "migration" is the set of steps needed whenever you change the structure
# of your SQLA models, add a new attribute, etc. to replicate those changes
# in the db, add a new column, a new table, etc.

app = FastAPI()

# We need to have an independent db session / connection (SessionLocal) per
# request, use the same session through all the request and then close it after
# the request is finished.

# Then a new session will be created for the next request.

# Our dependency will create a new SQLA SessionLocal that will be used in a
# single request, and then close it once the request is finished.

# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# We are creating the db session before each request in the dependency with
# 'yield', and then closing it afterwards.

# Then we can create the required dependency in the path operation function,
# to get that session directly.

# With that, we can just call crud.get_user directly from inside of the path
# operation function and use that session.


### User / Session Endpoints ###

#def create_user(schemas.UserCreate, db: Session = Depends(get_db)):
@app.post("/users/", response_model=schemas.UserOut)
def create_user(db: Session = Depends(get_db)):
    #db_user = crud.get_user_by_session_id(db, session_id=user.session_id)
    #if db_user:
    #    raise HTTPException(status_code=400, detail="Email already registered")
    # Notice that the values returned are SQLA models. But as all path operations
    # have a 'response_model' with Pydantic models / schemas using orm_mode,
    # the data declared in your Pydantic models will be extracted from them
    # and returned to the client, w/ all the normal filtering and validation.
    return crud.create_user(db=db)


# Type annotations in the function arguments will give you editor support
# inside of your function, with error checks, completion, etc.
# So, with that type declaration, FastAPI gives you automatic request "parsing".
# With the same Python type declaration, FastAPI gives you data validation.
# All the data validation is performed under the hood by Pydantic, so you get
# all the benefits from it.

### User / Session Endpoints ###

@app.get("/users/", response_model=list[schemas.User])
def read_users(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    users = crud.get_users(db, skip=skip, limit=limit)
    return users


@app.get("/users/{session_id}", response_model=schemas.User)
def read_user(session_id: str, db: Session = Depends(get_db)):
    db_user = crud.get_user(db, session_id=session_id)
    if db_user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return db_user

### Scenario Endpoints ###

@app.post("/scenario/{session_id}", response_model=schemas.ScenarioOut)
def create_scenario(
    session_id: str, scenario: schemas.ScenarioCreate,
    db: Session = Depends(get_db)
):
    return crud.create_scenario(
        db=db, scenario=scenario, session_id=session_id)


@app.patch("/scenario/{scenario_id}", status_code=200)
def update_scenario(
    scenario_id: int, scenario: schemas.ScenarioCreate,
    db: Session = Depends(get_db)
):
    return crud.update_scenario(
        db=db, scenario=scenario, scenario_id=scenario_id)


@app.delete("/scenario/{scenario_id}", status_code=200)
def delete_scenario(scenario_id: int, db: Session = Depends(get_db)):
    return crud.delete_scenario(db=db, scenario_id=scenario_id)


@app.get("/scenario/{scenario_id}", response_model=schemas.Scenario)
def read_scenario(scenario_id: int, db: Session = Depends(get_db)):
    db_scenario = crud.get_scenario(db, scenario_id=scenario_id)
    if db_scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return db_scenario


@app.get("/scenarios/", response_model=list[schemas.ScenarioAll])
def read_scenarios(session_id: str, db: Session = Depends(get_db)):
    scenarios = crud.get_scenarios(db, session_id=session_id)
    return scenarios


### Job Endpoints ###

@app.post("/jobs/", response_model=schemas.Job)
def create_job(
    job: schemas.JobCreate, db: Session = Depends(get_db)
):
    return crud.create_job(db=db, job=job)


@app.get("/job/{job_id}", response_model=schemas.JobStatus)
def read_job(job_id: int, db: Session = Depends(get_db)):
    db_job = crud.get_job(db, job_id=job_id)
    if db_job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return db_job

@app.get("/jobs/", response_model=list[schemas.Job])
def read_jobs(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    jobs = crud.get_jobs(db, skip=skip, limit=limit)
    return jobs


### Task Endpoints ###

@app.get("/pattern/{session_id}", response_model=list[schemas.PatternJob])
def create_pattern(session_id: str, db: Session = Depends(get_db), pattern: schemas.Pattern):
    #pattern_job = crud.create_pattern(db=db, session_id=session_id, pattern=pattern)
    #return pattern_job
    pass


@app.get("/lulc-table/{session_id}")
def lulc_under_parcel_summary(session_id: str, db: Session = Depends(get_db), wkt_parcel: str):
    #lulc_summary_table = crud.lulc_under_parcel_summary(db=db, session_id=session_id, pattern=pattern)
    #return lulc_summary_table
    pass


@app.get("/wallpapering/{session_id}/{scenario_id}")
def run_wallpapering(session_id: str, scenario_id: int, db: Session = Depends(get_db)):
    #wallpaper = crud.run_wallpaper(db=db, session_id=session_id, scenario_id=scenario_id)
    #return wallpaper
    pass

@app.get("/wallpapering/{job_id}")
def read_wallpapering_results(job_id: int, db: Session = Depends(get_db)):
    #wallpaper_results = crud.get_wallpaper_results(db=db, job_id=job_id)
    #return wallpaper_results
    pass


### Testing ideas from tutorial ###

client = TestClient(app)


def test_read_main():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"msg": "Hello World: prototype test"}

def test_add_jobs():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"msg": "Hello World"}

    # read status of job
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"msg": "Hello World"}
