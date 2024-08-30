""" Create SQLAlchemy models from the 'Base' class."""
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .database import Base

# SQLAlchemy uses 'model' to refer to these classes and instances that
# interact with the database.
# Pydantic uses term 'model' to refer to data validation, conversion, and
# documentation classes and instances


class Job(Base):
    """SQLAlchemy model to track jobs."""
    __tablename__ = "jobs"

    job_id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    description = Column(String)
    status = Column(String)
    # each job has an associated session owner
    owner_id = Column(String, ForeignKey("sessions.session_id"))

    owner = relationship("Session", back_populates="jobs")
    #jobs = relationship("ParcelStats", back_populates="jobs_id")


class Session(Base):
    """SQLAlchemy model for sessions."""
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, unique=True, index=True)
    last_active = Column(DateTime(timezone=True), server_default=func.now())

    # 'relationship' a "magic" attribute that will contain the values from
    # other tables related to this one.
    # When accessing the attribute 'items' in a User, as in 'my_user.items',
    # it will have a list of Item SQLAlchemy models (from the 'items' table)
    # that have a foreign key pointing to this record in the 'users' table.

    # When you access 'User.[jobs|scenarios|patterns]', SQLA will actually
    # go and fetch the jobs from the db in the corresponding table and
    # populate them here.
    study_areas = relationship("StudyArea", back_populates="owner")
    patterns = relationship("Pattern", back_populates="owner")
    jobs = relationship("Job", back_populates="owner")


class StudyArea(Base):
    """SQLAlchemy model for study areas."""
    __tablename__ = "study_area"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    # each study area has an associated session owner
    owner_id = Column(String, ForeignKey("sessions.session_id"))

    owner = relationship("Session", back_populates="study_areas")
    scenarios = relationship("Scenario", back_populates="study_area")
    parcels = relationship("Parcel", back_populates="study_area")


class Scenario(Base):
    """SQLAlchemy model for scenarios."""
    __tablename__ = "scenarios"

    scenario_id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    lulc_url_result = Column(String)
    lulc_stats = Column(String)
    lulc_url_base = Column(String, default="lulc_overlay_3857.tif")
    operation = Column(String)
    # each scenario has an associated study area owner
    study_area_id = Column(String, ForeignKey("study_area.id"))

    study_area = relationship("StudyArea", back_populates="scenarios")
    #invest_results = relationship("InvestResult", back_populates="scenario")


class Pattern(Base):
    """SQLAlchemy model for storing creating patterns."""
    __tablename__ = "patterns"

    pattern_id = Column(Integer, primary_key=True, index=True)
    label = Column(String, index=True)
    wkt = Column(String)
    pattern_thumbnail_path = Column(String)
    # each pattern has an associated session owner
    owner_id = Column(String, ForeignKey("sessions.session_id"))

    owner = relationship("Session", back_populates="patterns")


# TODO: It may make sense for this table to be global,
# rather than in context of a Session/User.
class ParcelStats(Base):
    """SQLAlchemy model for storing lulc stats under parcels."""
    __tablename__ = "parcel_stats"

    stats_id = Column(Integer, index=True, primary_key=True)
    parcel_id = Column(Integer)
    target_parcel_wkt = Column(String)
    lulc_stats = Column(String)
    job_id = Column(Integer, ForeignKey("jobs.job_id"))


class Parcel(Base):
    """SQLAlchemy model for parcels."""
    __tablename__ = "parcel"

    study_area_id = Column(String, ForeignKey("study_area.id"), primary_key=True)
    parcel_id = Column(Integer, ForeignKey("parcel_stats.parcel_id"), primary_key=True)
    wkt = Column(String)
    address = Column(String)

    study_area = relationship("StudyArea", back_populates="parcels")
    parcel_stats = relationship("ParcelStats", uselist=False)


class InvestResult(Base):
    """SQLAlchemy model for InVEST results."""
    __tablename__ = "invest_results"

    scenario_id = Column(Integer, ForeignKey("scenarios.scenario_id"), primary_key=True)
    job_id = Column(Integer, ForeignKey("jobs.job_id"), primary_key=True)
    model_name = Column(String)
    result = Column(String)
    serviceshed = Column(String)

    #scenario = relationship("Scenario", back_populates="invest_results")


class LulcCrosswalk(Base):
    """Lookup table for landuse-landcover codes and labels."""
    __tablename__ = "lulc_crosswalk"

    lucode = Column(Integer, primary_key=True)
    nlud_simple_class = Column(String)
    nlud_simple_subclass = Column(String)
    nlud_simple = Column(Integer)
    nlcd = Column(Integer)
    nlcd_lulc = Column(String)
    tree = Column(Integer)
    tree_canopy_cover = Column(String)
    is_realistic_to_create = Column(String)
