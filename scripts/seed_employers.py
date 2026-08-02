"""Seed Scout's employer watchlist with the researched 47-employer tech list.

Run from the repo root with the venv active:

    python scripts/seed_employers.py

Idempotent — re-running updates aliases/sector/priority/notes on existing rows
and never duplicates. `watching` is deliberately NOT reset on update: if you've
switched an employer off in the dashboard, re-seeding leaves it off.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Priority: 1 = high (Cloud Security fit), 2 = medium, 3 = low
EMPLOYERS = [
    # BIG TECH
    ("Amazon", "Amazon UK,AMAZON UK SERVICES", "Big Tech", 1, "AWS+DevOps+SDE cloud-security relevant. Rolling, apply Nov."),
    ("IBM", "IBM UK,IBM UNITED KINGDOM", "Big Tech", 1, "1-week window in Feb."),
    ("Microsoft", "Microsoft UK,MICROSOFT LIMITED", "Big Tech", 1, "Cyber pathway + Get Into Digital."),
    ("Google", "Google UK,GOOGLE UK LIMITED", "Big Tech", 2, "Level 4 only."),
    ("Cisco", "Cisco Systems,CISCO INTERNATIONAL", "Big Tech", 1, "Networking gold standard, CCNA."),
    ("Samsung", "Samsung Electronics,SAMSUNG ELECTRONICS", "Big Tech", 3, ""),
    # CONSULTING
    ("Accenture", "ACCENTURE UK,Accenture PLC", "Consulting", 1, "Software Eng spec = AWS+DevOps."),
    ("Capgemini", "CAPGEMINI UK,Capgemini", "Consulting", 1, "Cloud & Custom App pathway."),
    ("Deloitte", "DELOITTE LLP,Deloitte MCS", "Consulting", 2, "Bright Start, rolling from Sept."),
    ("PwC", "PricewaterhouseCoopers,PWC LLP", "Consulting", 2, "Flying Start, Belfast hub."),
    ("KPMG", "KPMG LLP,KPMG UK", "Consulting", 2, "High volume, apply Sept."),
    ("EY", "Ernst & Young,EY LLP,ERNST YOUNG", "Consulting", 2, ""),
    ("TCS", "Tata Consultancy Services,TCS Limited", "Consulting", 2, ""),
    ("Fujitsu", "Fujitsu Services,FUJITSU SERVICES LIMITED", "Consulting", 1, "Explicit Cyber pathway."),
    ("FDM Group", "FDM Group,FDM GROUP LIMITED", "Consulting", 2, ""),
    ("Cognizant", "Cognizant Technology,COGNIZANT WORLDWIDE", "Consulting", 2, ""),
    # FINANCE
    ("JP Morgan", "JPMorgan Chase,JPMORGAN CHASE BANK,JP Morgan Chase", "Finance", 1, "Bournemouth hub, Exeter."),
    ("Goldman Sachs", "Goldman Sachs International,GOLDMAN SACHS", "Finance", 1, "London QMUL / Birmingham WMG."),
    ("HSBC", "HSBC UK,HSBC HOLDINGS,HSBC BANK", "Finance", 1, "Explicitly welcomes T-Level."),
    ("Barclays", "Barclays Bank,BARCLAYS PLC,BARCLAYS SERVICES", "Finance", 1, "Cyber pathway direct fit."),
    ("Lloyds", "Lloyds Bank,Lloyds Banking Group,LLOYDS BANK PLC", "Finance", 1, "Cyber in syllabus."),
    ("NatWest", "NatWest Group,NATIONAL WESTMINSTER BANK,NatWest Bank", "Finance", 1, ""),
    ("Bank of England", "BANK OF ENGLAND", "Finance", 2, "Data Science, Northeastern London."),
    ("Bank of America", "Bank of America,MERRILL LYNCH,BOFA", "Finance", 2, ""),
    ("Aviva", "AVIVA PLC,Aviva Insurance", "Finance", 2, ""),
    ("Nationwide", "NATIONWIDE BUILDING SOCIETY", "Finance", 2, ""),
    # DEFENCE / PUBLIC
    ("BAE Systems", "BAE SYSTEMS PLC,BAE Systems Applied Intelligence", "Defence/Public", 1, "Cyber L6, UK national."),
    ("GCHQ", "Government Communications Headquarters,CyberFirst", "Defence/Public", 1, "CyberFirst Sept-Nov. UK CITIZEN ONLY."),
    ("DSTL", "Defence Science and Technology Laboratory,Dstl", "Defence/Public", 1, "Civil Service Jobs route."),
    ("Ministry of Defence", "MOD,Ministry of Defence,MINISTRY OF DEFENCE", "Defence/Public", 1, ""),
    ("Home Office", "Home Office,HOME OFFICE", "Defence/Public", 1, "Salford cyber £38-41k."),
    ("NCA", "National Crime Agency,NATIONAL CRIME AGENCY", "Defence/Public", 1, ""),
    ("MBDA", "MBDA UK,MBDA SYSTEMS", "Defence/Public", 2, ""),
    ("NHS Digital", "NHS Digital,NHS England,NHS BUSINESS SERVICES", "Defence/Public", 2, ""),
    # TELECOMS / MEDIA
    ("BT Group", "BT Group,British Telecommunications,BT PLC", "Telecoms/Media", 1, "Salford cyber hub, Sapia AI interview."),
    ("Vodafone", "Vodafone,VodafoneThree,VODAFONE LIMITED", "Telecoms/Media", 2, ""),
    ("Sky", "Sky UK,SKY UK LIMITED,SKY BROADCASTING", "Telecoms/Media", 2, ""),
    ("BBC", "British Broadcasting Corporation,BBC PUBLIC SERVICES", "Telecoms/Media", 2, ""),
    ("Virgin Media O2", "Virgin Media,Telefonica UK,VIRGIN MEDIA O2", "Telecoms/Media", 2, ""),
    # AUTOMOTIVE / MFG
    ("Jaguar Land Rover", "JAGUAR - LAND ROVER LTD,JLR,Jaguar Land Rover", "Automotive/Mfg", 2, ""),
    ("Rolls-Royce", "Rolls-Royce PLC,ROLLS-ROYCE plc", "Automotive/Mfg", 2, ""),
    ("Airbus", "Airbus,AIRBUS OPERATIONS", "Automotive/Mfg", 2, ""),
    # RETAIL / OTHER
    ("Ocado", "Ocado Technology,OCADO GROUP", "Retail/Other", 2, ""),
    ("John Lewis", "John Lewis Partnership,JOHN LEWIS PLC", "Retail/Other", 2, ""),
    ("Tesco", "TESCO PLC,Tesco Stores", "Retail/Other", 3, ""),
    ("Transport for London", "TfL,TRANSPORT FOR LONDON", "Retail/Other", 2, ""),
    ("Nestle", "Nestle UK,NESTLE UK LTD", "Retail/Other", 3, ""),
]


def seed(app=None) -> dict:
    """Upsert every employer. Returns {'created': n, 'updated': n, 'total': n}."""
    from models import Employer
    from models import db as orm

    if app is None:
        import app as asfa_app
        app = asfa_app.app

    created = updated = 0
    with app.app_context():
        for name, aliases, sector, priority, notes in EMPLOYERS:
            existing = Employer.query.filter_by(name=name).first()
            if existing:
                existing.aliases = aliases
                existing.sector = sector
                existing.priority = priority
                existing.notes = notes
                updated += 1
            else:
                orm.session.add(Employer(
                    name=name, aliases=aliases, sector=sector,
                    priority=priority, notes=notes, watching=True,
                ))
                created += 1
        orm.session.commit()
        total = Employer.query.count()
    return {"created": created, "updated": updated, "total": total}


if __name__ == "__main__":
    result = seed()
    print(f"Employers seeded — {result['created']} created, "
          f"{result['updated']} updated, {result['total']} total.")
