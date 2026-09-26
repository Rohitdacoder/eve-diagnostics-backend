"""Load some sample centres and tests. Safe to run more than once.

    python -m app.scripts.seed
"""
from decimal import Decimal

from sqlalchemy import select

from app.database import SessionLocal
from app.models import CentreTest, DiagnosticCentre, DiagnosticTest

TESTS = {
    "Complete Blood Count (CBC)": "Measures red cells, white cells and platelets",
    "Lipid Profile": "Cholesterol and triglycerides",
    "Thyroid Profile (T3, T4, TSH)": None,
    "HbA1c": "Average blood sugar over the last 3 months",
    "Vitamin D": None,
    "Chest X-Ray": None,
    "MRI Brain": None,
}

CENTRES = [
    ("City Diagnostics", "Delhi", {"Complete Blood Count (CBC)": "350", "Lipid Profile": "600", "HbA1c": "450", "Chest X-Ray": "500"}),
    ("HealthFirst Labs", "Mumbai", {"Complete Blood Count (CBC)": "400", "Thyroid Profile (T3, T4, TSH)": "550", "Vitamin D": "1200"}),
    ("CarePoint Imaging", "Bengaluru", {"Chest X-Ray": "650", "MRI Brain": "6500"}),
    ("City Diagnostics", "Pune", {"Complete Blood Count (CBC)": "300", "Lipid Profile": "550", "Vitamin D": "1100"}),
]


def main():
    with SessionLocal() as db:
        tests = {}
        for name, desc in TESTS.items():
            test = db.scalar(select(DiagnosticTest).where(DiagnosticTest.name == name))
            if test is None:
                test = DiagnosticTest(name=name, description=desc)
                db.add(test)
            tests[name] = test
        db.flush()

        for name, location, prices in CENTRES:
            centre = db.scalar(
                select(DiagnosticCentre).where(
                    DiagnosticCentre.name == name, DiagnosticCentre.location == location
                )
            )
            if centre is None:
                centre = DiagnosticCentre(name=name, location=location)
                db.add(centre)
                db.flush()
            for test_name, price in prices.items():
                if db.get(CentreTest, (centre.id, tests[test_name].id)) is None:
                    db.add(CentreTest(centre_id=centre.id, test_id=tests[test_name].id, price=Decimal(price)))

        db.commit()
    print("seed done")


if __name__ == "__main__":
    main()
