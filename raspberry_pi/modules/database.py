"""
Smart Medication System - Database Module

SQLite database for logging medication events, tracking compliance,
and storing registered medicine records from tag/QR/OCR onboarding.
"""

import sqlite3
import json
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from threading import Lock

class Database:
    """
    SQLite database for medication event logging
    
    Tables:
    - medication_events: All medication intake events
    - compliance_history: Daily compliance summaries
    - registered_medicines: Registered medicine records from tag/QR/OCR onboarding
    """
    
    def __init__(self, config: dict, logger):
        """
        Initialize database
        
        Args:
            config: Database configuration
            logger: Logger instance
        """
        self.config = config
        self.logger = logger
        
        # Database path
        db_path = config.get('path', 'data/medication_events.db')
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Connection
        self.connection = None
        self.db_lock = Lock()
        
        self.logger.info(f"Database initialized: {self.db_path}")
    
    def connect(self) -> bool:
        """
        Connect to database and create tables
        
        Returns:
            True if successful
        """
        try:
            self.connection = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False
            )
            self.connection.row_factory = sqlite3.Row
            
            self._create_tables()
            
            self.logger.info("Database connected")
            return True
            
        except Exception as e:
            self.logger.error(f"Database connection failed: {e}")
            return False
    
    def _create_tables(self):
        """Create database tables if they don't exist"""
        with self.db_lock:
            cursor = self.connection.cursor()
            
            # Medication events table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS medication_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    date TEXT NOT NULL,
                    time TEXT NOT NULL,
                    medicine_name TEXT NOT NULL,
                    expected_dosage INTEGER NOT NULL,
                    actual_dosage INTEGER,
                    result TEXT NOT NULL,
                    verified INTEGER NOT NULL,
                    ocr_verified INTEGER,
                    weight_verified INTEGER,
                    behavior_verified INTEGER,
                    overall_score REAL,
                    alerts TEXT,
                    details TEXT,
                    UNIQUE(timestamp)
                )
            ''')
            
            # Create index on date for faster queries
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_events_date 
                ON medication_events(date)
            ''')
            
            # Compliance history table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS compliance_history (
                    date TEXT PRIMARY KEY,
                    total_scheduled INTEGER NOT NULL,
                    taken_correctly INTEGER NOT NULL,
                    taken_incorrectly INTEGER NOT NULL,
                    missed INTEGER NOT NULL,
                    compliance_rate REAL NOT NULL,
                    behavioral_issues INTEGER NOT NULL
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS registered_medicines (
                    medicine_id TEXT PRIMARY KEY,
                    patient_id TEXT,
                    medicine_name TEXT NOT NULL,
                    dosage_amount INTEGER,
                    dosage_unit TEXT,
                    time_slots TEXT,
                    meal_rule TEXT,
                    station_id TEXT,
                    tag_uid TEXT UNIQUE,
                    tag_payload TEXT,
                    source_method TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            ''')
            
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_registered_medicines_tag_uid
                ON registered_medicines(tag_uid)
            ''')
            
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_registered_medicines_station
                ON registered_medicines(station_id)
            ''')

            # Unique index: no two *active* rows may share the same medicine_name
            # for the same patient.  COALESCE normalises NULL patient_id so that
            # the constraint fires even when patient_id is not set on the tag.
            cursor.execute('''
                CREATE UNIQUE INDEX IF NOT EXISTS uq_active_medicine_name_per_patient
                ON registered_medicines(COALESCE(patient_id, ''), medicine_name)
                WHERE active = 1
            ''')

            self.connection.commit()
            self.logger.info("Database tables created/verified")
    
    def log_medication_event(self, decision: Dict[str, Any]) -> bool:
        """
        Log medication event to database
        
        Args:
            decision: Decision engine result
            
        Returns:
            True if successful
        """
        try:
            with self.db_lock:
                cursor = self.connection.cursor()
                
                timestamp = decision.get('timestamp', time.time())
                dt = datetime.fromtimestamp(timestamp)
                
                cursor.execute('''
                    INSERT OR REPLACE INTO medication_events (
                        timestamp, date, time,
                        medicine_name, expected_dosage, actual_dosage,
                        result, verified,
                        ocr_verified, weight_verified, behavior_verified,
                        overall_score, alerts, details
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    timestamp,
                    dt.strftime('%Y-%m-%d'),
                    dt.strftime('%H:%M:%S'),
                    decision.get('expected_medicine', ''),
                    decision.get('expected_dosage', 0),
                    decision.get('details', {}).get('weight_actual'),
                    decision.get('result').value if decision.get('result') else 'unknown',
                    1 if decision.get('verified') else 0,
                    1 if decision.get('details', {}).get('ocr_match') else 0,
                    1 if decision.get('details', {}).get('weight_within_tolerance') else 0,
                    1 if decision.get('details', {}).get('behavior_status') in ['good', 'acceptable'] else 0,
                    decision.get('scores', {}).get('overall', 0.0),
                    json.dumps(decision.get('alerts', [])),
                    json.dumps(decision.get('details', {}))
                ))
                
                self.connection.commit()
                
                self.logger.info(
                    f"Event logged: {decision.get('expected_medicine')} "
                    f"({decision.get('result').value if decision.get('result') else 'unknown'})"
                )
                
                return True
                
        except Exception as e:
            self.logger.error(f"Failed to log event: {e}")
            return False
            
    def get_events_by_date(self, date_str: str) -> List[Dict[str, Any]]:
        """
        Get all events for a specific date
        
        Args:
            date_str: Date in YYYY-MM-DD format
            
        Returns:
            List of event dictionaries
        """
        try:
            with self.db_lock:
                cursor = self.connection.cursor()
                
                cursor.execute('''
                    SELECT * FROM medication_events
                    WHERE date = ?
                    ORDER BY timestamp
                ''', (date_str,))
                
                rows = cursor.fetchall()
                
                events = []
                for row in rows:
                    events.append({
                        'id': row['id'],
                        'timestamp': row['timestamp'],
                        'date': row['date'],
                        'time': row['time'],
                        'medicine_name': row['medicine_name'],
                        'expected_dosage': row['expected_dosage'],
                        'actual_dosage': row['actual_dosage'],
                        'result': row['result'],
                        'verified': bool(row['verified']),
                        'overall_score': row['overall_score'],
                        'alerts': json.loads(row['alerts']) if row['alerts'] else [],
                        'details': json.loads(row['details']) if row['details'] else {}
                    })
                
                return events
                
        except Exception as e:
            self.logger.error(f"Failed to get events: {e}")
            return []
    
    def get_todays_events(self) -> List[Dict[str, Any]]:
        """Get all events for today"""
        today = datetime.now().strftime('%Y-%m-%d')
        return self.get_events_by_date(today)
    
    def calculate_daily_compliance(self, date_str: str, total_scheduled: int) -> Dict[str, Any]:
        """
        Calculate compliance statistics for a date
        
        Args:
            date_str: Date in YYYY-MM-DD format
            total_scheduled: Total medications scheduled for the day
            
        Returns:
            Compliance statistics
        """
        events = self.get_events_by_date(date_str)
        
        taken_correctly = sum(1 for e in events if e['verified'])
        taken_incorrectly = sum(1 for e in events if not e['verified'] and e['result'] != 'no_intake')
        
        # Missed = scheduled - taken
        total_taken = len(events)
        missed = max(0, total_scheduled - total_taken)
        
        # Behavioral issues
        behavioral_issues = sum(
            1 for e in events 
            if e['result'] == 'behavioral_issue'
        )
        
        # Compliance rate
        if total_scheduled > 0:
            compliance_rate = (taken_correctly / total_scheduled) * 100
        else:
            compliance_rate = 0.0
        
        compliance_data = {
            'date': date_str,
            'total_scheduled': total_scheduled,
            'taken_correctly': taken_correctly,
            'taken_incorrectly': taken_incorrectly,
            'missed': missed,
            'compliance_rate': compliance_rate,
            'behavioral_issues': behavioral_issues
        }
        
        return compliance_data
        
    def save_daily_compliance(self, compliance_data: Dict[str, Any]) -> bool:
        """
        Save daily compliance summary
        
        Args:
            compliance_data: Compliance statistics
            
        Returns:
            True if successful
        """
        try:
            with self.db_lock:
                cursor = self.connection.cursor()
                
                cursor.execute('''
                    INSERT OR REPLACE INTO compliance_history (
                        date, total_scheduled, taken_correctly, taken_incorrectly,
                        missed, compliance_rate, behavioral_issues
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    compliance_data['date'],
                    compliance_data['total_scheduled'],
                    compliance_data['taken_correctly'],
                    compliance_data['taken_incorrectly'],
                    compliance_data['missed'],
                    compliance_data['compliance_rate'],
                    compliance_data['behavioral_issues']
                ))
                
                self.connection.commit()
                
                self.logger.info(f"Compliance saved for {compliance_data['date']}")
                return True
                
        except Exception as e:
            self.logger.error(f"Failed to save compliance: {e}")
            return False
    
    def get_compliance_history(self, days: int = 7) -> List[Dict[str, Any]]:
        """
        Get compliance history for last N days
        
        Args:
            days: Number of days
            
        Returns:
            List of compliance dictionaries
        """
        try:
            with self.db_lock:
                cursor = self.connection.cursor()
                
                # Calculate start date
                start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
                
                cursor.execute('''
                    SELECT * FROM compliance_history
                    WHERE date >= ?
                    ORDER BY date DESC
                ''', (start_date,))
                
                rows = cursor.fetchall()
                
                history = []
                for row in rows:
                    history.append({
                        'date': row['date'],
                        'total_scheduled': row['total_scheduled'],
                        'taken_correctly': row['taken_correctly'],
                        'taken_incorrectly': row['taken_incorrectly'],
                        'missed': row['missed'],
                        'compliance_rate': row['compliance_rate'],
                        'behavioral_issues': row['behavioral_issues']
                    })
                
                return history
                
        except Exception as e:
            self.logger.error(f"Failed to get compliance history: {e}")
            return []
            
    def get_statistics(self) -> Dict[str, Any]:
        """
        Get overall statistics
        
        Returns:
            Statistics dictionary
        """
        try:
            with self.db_lock:
                cursor = self.connection.cursor()
                
                # Total events
                cursor.execute('SELECT COUNT(*) as total FROM medication_events')
                total_events = cursor.fetchone()['total']
                
                # Verified events
                cursor.execute('SELECT COUNT(*) as verified FROM medication_events WHERE verified = 1')
                verified_events = cursor.fetchone()['verified']
                
                # Average compliance (last 7 days)
                cursor.execute('''
                    SELECT AVG(compliance_rate) as avg_compliance
                    FROM compliance_history
                    WHERE date >= date('now', '-7 days')
                ''')
                avg_compliance = cursor.fetchone()['avg_compliance'] or 0.0
                
                return {
                    'total_events': total_events,
                    'verified_events': verified_events,
                    'avg_compliance_7days': avg_compliance
                }
                
        except Exception as e:
            self.logger.error(f"Failed to get statistics: {e}")
            return {}
    
    def upsert_registered_medicine(self, record: Dict[str, Any]) -> bool:
        """
        Insert or update a registered medicine from tag/QR/OCR onboarding.

        Duplicate-safe: the check and the write happen inside the same
        db_lock acquisition so concurrent onboarding calls cannot both
        slip through the application-level pre-check.

        Returns False (without raising) when:
          • the medicine_id is already active on a *different* station, or
          • the medicine_name already exists for the same patient (active), or
          • any other database error occurs.
        """
        try:
            with self.db_lock:
                cursor = self.connection.cursor()

                medicine_id      = record.get("medicine_id")
                incoming_station = record.get("station_id")
                medicine_name    = record.get("medicine_name")
                patient_id       = record.get("patient_id")  # may be None

                # ----------------------------------------------------------
                # Atomic duplicate guards
                # ----------------------------------------------------------

                # Guard 1 – same medicine_id already active on a different station
                cursor.execute(
                    "SELECT station_id FROM registered_medicines "
                    "WHERE medicine_id = ? AND active = 1",
                    (medicine_id,),
                )
                existing_by_id = cursor.fetchone()
                if existing_by_id is not None:
                    existing_station = existing_by_id["station_id"]
                    if existing_station != incoming_station:
                        self.logger.warning(
                            f"Duplicate blocked: medicine_id '{medicine_id}' is "
                            f"already active on station '{existing_station}'; "
                            f"refusing registration to '{incoming_station}'"
                        )
                        return False
                    # Same station → fall through; re-registration is allowed.

                # Guard 2 – same medicine_name already active for the same patient
                cursor.execute(
                    "SELECT medicine_id FROM registered_medicines "
                    "WHERE COALESCE(patient_id, '') = ? "
                    "  AND medicine_name = ? "
                    "  AND active = 1 "
                    "  AND medicine_id != ?",
                    (patient_id or "", medicine_name, medicine_id),
                )
                existing_by_name = cursor.fetchone()
                if existing_by_name is not None:
                    self.logger.warning(
                        f"Duplicate blocked: medicine_name '{medicine_name}' is "
                        f"already registered (id='{existing_by_name['medicine_id']}') "
                        f"for patient '{patient_id}'"
                    )
                    return False

                # ----------------------------------------------------------
                # Write
                # ----------------------------------------------------------
                now_ts = time.time()

                cursor.execute('''
                    INSERT INTO registered_medicines (
                        medicine_id,
                        patient_id,
                        medicine_name,
                        dosage_amount,
                        dosage_unit,
                        time_slots,
                        meal_rule,
                        station_id,
                        tag_uid,
                        tag_payload,
                        source_method,
                        active,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(medicine_id) DO UPDATE SET
                        patient_id    = excluded.patient_id,
                        medicine_name = excluded.medicine_name,
                        dosage_amount = excluded.dosage_amount,
                        dosage_unit   = excluded.dosage_unit,
                        time_slots    = excluded.time_slots,
                        meal_rule     = excluded.meal_rule,
                        station_id    = excluded.station_id,
                        tag_uid       = excluded.tag_uid,
                        tag_payload   = excluded.tag_payload,
                        source_method = excluded.source_method,
                        active        = excluded.active,
                        updated_at    = excluded.updated_at
                ''', (
                    medicine_id,
                    patient_id,
                    medicine_name,
                    record.get("dosage_amount"),
                    record.get("dosage_unit", "TABLET"),
                    record.get("time_slots"),
                    record.get("meal_rule"),
                    incoming_station,
                    record.get("tag_uid"),
                    record.get("tag_payload"),
                    record.get("source_method", "tag"),
                    1 if record.get("active", True) else 0,
                    record.get("created_at", now_ts),
                    now_ts,
                ))

                self.connection.commit()
                self.logger.info(f"Registered medicine upserted: {medicine_id}")
                return True

        except Exception as e:
            self.logger.error(f"Failed to upsert registered medicine: {e}")
            return False

    def get_registered_medicine_by_tag_uid(self, tag_uid: str) -> Optional[Dict[str, Any]]:
        """
        Get registered medicine by tag UID.
        """
        try:
            with self.db_lock:
                cursor = self.connection.cursor()
                
                cursor.execute('''
                    SELECT * FROM registered_medicines
                    WHERE tag_uid = ? AND active = 1
                    LIMIT 1
                ''', (tag_uid,))
                
                row = cursor.fetchone()
                if not row:
                    return None
                
                return {
                    "medicine_id": row["medicine_id"],
                    "patient_id": row["patient_id"],
                    "medicine_name": row["medicine_name"],
                    "dosage_amount": row["dosage_amount"],
                    "dosage_unit": row["dosage_unit"],
                    "time_slots": row["time_slots"],
                    "meal_rule": row["meal_rule"],
                    "station_id": row["station_id"],
                    "tag_uid": row["tag_uid"],
                    "tag_payload": row["tag_payload"],
                    "source_method": row["source_method"],
                    "active": bool(row["active"]),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"]
                }
                
        except Exception as e:
            self.logger.error(f"Failed to get registered medicine by tag UID: {e}")
            return None

    def get_registered_medicine_by_id(self, medicine_id: str) -> Optional[Dict[str, Any]]:
        """
        Get registered medicine by medicine ID.
        """
        try:
            with self.db_lock:
                cursor = self.connection.cursor()
                
                cursor.execute('''
                    SELECT * FROM registered_medicines
                    WHERE medicine_id = ? AND active = 1
                    LIMIT 1
                ''', (medicine_id,))
                
                row = cursor.fetchone()
                if not row:
                    return None
                
                return {
                    "medicine_id": row["medicine_id"],
                    "patient_id": row["patient_id"],
                    "medicine_name": row["medicine_name"],
                    "dosage_amount": row["dosage_amount"],
                    "dosage_unit": row["dosage_unit"],
                    "time_slots": row["time_slots"],
                    "meal_rule": row["meal_rule"],
                    "station_id": row["station_id"],
                    "tag_uid": row["tag_uid"],
                    "tag_payload": row["tag_payload"],
                    "source_method": row["source_method"],
                    "active": bool(row["active"]),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"]
                }
                
        except Exception as e:
            self.logger.error(f"Failed to get registered medicine by ID: {e}")
            return None
            
    def list_registered_medicines(self) -> List[Dict[str, Any]]:
        """
        Get all active registered medicines.
        """
        try:
            with self.db_lock:
                cursor = self.connection.cursor()

                cursor.execute('''
                    SELECT * FROM registered_medicines
                    WHERE active = 1
                    ORDER BY medicine_id
                ''')

                rows = cursor.fetchall()

                records = []
                for row in rows:
                    records.append({
                        "medicine_id": row["medicine_id"],
                        "patient_id": row["patient_id"],
                        "medicine_name": row["medicine_name"],
                        "dosage_amount": row["dosage_amount"],
                        "dosage_unit": row["dosage_unit"],
                        "time_slots": row["time_slots"],
                        "meal_rule": row["meal_rule"],
                        "station_id": row["station_id"],
                        "tag_uid": row["tag_uid"],
                        "tag_payload": row["tag_payload"],
                        "source_method": row["source_method"],
                        "active": bool(row["active"]),
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                    })

                return records

        except Exception as e:
            self.logger.error(f"Failed to list registered medicines: {e}")
            return []

    def get_registered_medicine_by_station(self, station_id: str) -> Optional[Dict[str, Any]]:
        """
        Get registered medicine assigned to a station.
        """
        try:
            with self.db_lock:
                cursor = self.connection.cursor()

                cursor.execute('''
                    SELECT * FROM registered_medicines
                    WHERE station_id = ? AND active = 1
                    LIMIT 1
                ''', (station_id,))

                row = cursor.fetchone()
                if not row:
                    return None

                return {
                    "medicine_id": row["medicine_id"],
                    "patient_id": row["patient_id"],
                    "medicine_name": row["medicine_name"],
                    "dosage_amount": row["dosage_amount"],
                    "dosage_unit": row["dosage_unit"],
                    "time_slots": row["time_slots"],
                    "meal_rule": row["meal_rule"],
                    "station_id": row["station_id"],
                    "tag_uid": row["tag_uid"],
                    "tag_payload": row["tag_payload"],
                    "source_method": row["source_method"],
                    "active": bool(row["active"]),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }

        except Exception as e:
            self.logger.error(f"Failed to get registered medicine by station: {e}")
            return None
            
    def assign_station_to_medicine(self, medicine_id: str, station_id: str) -> bool:
        """
        Update station assignment for a registered medicine.
        """
        try:
            with self.db_lock:
                cursor = self.connection.cursor()

                cursor.execute('''
                    UPDATE registered_medicines
                    SET station_id = ?, updated_at = ?
                    WHERE medicine_id = ?
                ''', (
                    station_id,
                    time.time(),
                    medicine_id
                ))

                self.connection.commit()

                if cursor.rowcount == 0:
                    self.logger.warning(
                        f"No registered medicine found for station assignment: {medicine_id}"
                    )
                    return False

                self.logger.info(
                    f"Assigned {medicine_id} to {station_id}"
                )
                return True

        except Exception as e:
            self.logger.error(f"Failed to assign station to medicine: {e}")
            return False
    
    def cleanup(self):
        """Close database connection"""
        if self.connection:
            self.connection.close()
            self.logger.info("Database connection closed")
