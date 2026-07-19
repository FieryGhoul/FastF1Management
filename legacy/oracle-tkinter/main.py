import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import cx_Oracle
import threading
from flask import Flask, jsonify, request
from datetime import datetime
import sys
import uuid

# Flask app initialization
app = Flask(__name__)
flask_thread = None

# Database connection parameters - UPDATE THESE TO MATCH YOUR ORACLE SETUP
DB_USER = "system"  # Replace with your Oracle username
DB_PASSWORD = "system"  # Replace with your Oracle password
DB_DSN = "localhost:1521/XE"  # Replace with your Oracle DSN

# Helper functions for database operations
def get_db_connection():
    try:
        return cx_Oracle.connect(DB_USER, DB_PASSWORD, DB_DSN)
    except cx_Oracle.Error as error:
        print(f"Database connection error: {error}")
        raise

def execute_query(query, params=None, fetch=True):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
        
        if fetch:
            return cursor.fetchall()
        else:
            conn.commit()
            return cursor.rowcount
    except cx_Oracle.Error as error:
        if conn:
            conn.rollback()
        print(f"Database error: {error}")
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def setup_audit_log():
    # """Create audit log table and trigger if they don't exist"""
    # create_table = """
    # CREATE TABLE Audit_Log (
    #     Audit_ID VARCHAR2(36),
    #     Action_Type VARCHAR2(10),
    #     Table_Name VARCHAR2(50),
    #     Record_ID VARCHAR2(100),
    #     Action_Details VARCHAR2(1000),
    #     Action_By VARCHAR2(50),
    #     Action_Date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    #     PRIMARY KEY (Audit_ID)
    # )
    # """
    
    create_trigger = """
    CREATE OR REPLACE TRIGGER audit_trigger
    AFTER INSERT OR UPDATE OR DELETE ON Team
    FOR EACH ROW
    DECLARE
        v_action VARCHAR2(10);
        v_record_id VARCHAR2(100);
        v_details VARCHAR2(1000);
    BEGIN
        IF INSERTING THEN
            v_action := 'INSERT';
            v_record_id := :NEW.Team_Name || '-' || :NEW.Year;
            v_details := 'Team: ' || :NEW.Team_Name || ', Score: ' || :NEW.Team_Score || 
                        ', Principal: ' || :NEW.Principal_First_Name || ' ' || :NEW.Principal_Last_Name;
        ELSIF UPDATING THEN
            v_action := 'UPDATE';
            v_record_id := :NEW.Team_Name || '-' || :NEW.Year;
            v_details := 'Old Score: ' || :OLD.Team_Score || ', New Score: ' || :NEW.Team_Score || 
                        ', Old Principal: ' || :OLD.Principal_First_Name || ' ' || :OLD.Principal_Last_Name ||
                        ', New Principal: ' || :NEW.Principal_First_Name || ' ' || :NEW.Principal_Last_Name;
        ELSE
            v_action := 'DELETE';
            v_record_id := :OLD.Team_Name || '-' || :OLD.Year;
            v_details := 'Team: ' || :OLD.Team_Name || ', Score: ' || :OLD.Team_Score;
        END IF;
        
        INSERT INTO Audit_Log (
            Audit_ID,
            Action_Type,
            Table_Name,
            Record_ID,
            Action_Details,
            Action_By
        ) VALUES (
            SYS_GUID(),
            v_action,
            'Team',
            v_record_id,
            v_details,
            USER
        );
    END;
    """
    
    create_driver_trigger = """
    CREATE OR REPLACE TRIGGER audit_driver_trigger
    AFTER INSERT OR UPDATE OR DELETE ON Driver
    FOR EACH ROW
    DECLARE
        v_action VARCHAR2(10);
        v_record_id VARCHAR2(100);
        v_details VARCHAR2(1000);
    BEGIN
        IF INSERTING THEN
            v_action := 'INSERT';
            v_record_id := :NEW.Driver_ID || '-' || :NEW.Year;
            v_details := 'Driver: ' || :NEW.First_Name || ' ' || :NEW.Last_Name || 
                        ', Team: ' || :NEW.Team_Name || ', Score: ' || :NEW.Total_Ind_Score;
        ELSIF UPDATING THEN
            v_action := 'UPDATE';
            v_record_id := :NEW.Driver_ID || '-' || :NEW.Year;
            v_details := 'Old Name: ' || :OLD.First_Name || ' ' || :OLD.Last_Name || 
                        ', New Name: ' || :NEW.First_Name || ' ' || :NEW.Last_Name ||
                        ', Old Team: ' || :OLD.Team_Name || ', New Team: ' || :NEW.Team_Name ||
                        ', Old Score: ' || :OLD.Total_Ind_Score || ', New Score: ' || :NEW.Total_Ind_Score;
        ELSE
            v_action := 'DELETE';
            v_record_id := :OLD.Driver_ID || '-' || :OLD.Year;
            v_details := 'Driver: ' || :OLD.First_Name || ' ' || :OLD.Last_Name || 
                        ', Team: ' || :OLD.Team_Name;
        END IF;
        
        INSERT INTO Audit_Log (
            Audit_ID,
            Action_Type,
            Table_Name,
            Record_ID,
            Action_Details,
            Action_By
        ) VALUES (
            SYS_GUID(),
            v_action,
            'Driver',
            v_record_id,
            v_details,
            USER
        );
    END;
    """
    
    create_race_trigger = """
    CREATE OR REPLACE TRIGGER audit_race_trigger
    AFTER INSERT OR UPDATE OR DELETE ON Race
    FOR EACH ROW
    DECLARE
        v_action VARCHAR2(10);
        v_record_id VARCHAR2(100);
        v_details VARCHAR2(1000);
    BEGIN
        IF INSERTING THEN
            v_action := 'INSERT';
            v_record_id := :NEW.Race_Name || '-' || :NEW.Year;
            v_details := 'Race: ' || :NEW.Race_Name || ', Date: ' || TO_CHAR(:NEW.Race_Date, 'YYYY-MM-DD') || 
                        ', Circuit: ' || :NEW.Circuit_Name;
        ELSIF UPDATING THEN
            v_action := 'UPDATE';
            v_record_id := :NEW.Race_Name || '-' || :NEW.Year;
            v_details := 'Old Date: ' || TO_CHAR(:OLD.Race_Date, 'YYYY-MM-DD') || 
                        ', New Date: ' || TO_CHAR(:NEW.Race_Date, 'YYYY-MM-DD') ||
                        ', Old Circuit: ' || :OLD.Circuit_Name || ', New Circuit: ' || :NEW.Circuit_Name;
        ELSE
            v_action := 'DELETE';
            v_record_id := :OLD.Race_Name || '-' || :OLD.Year;
            v_details := 'Race: ' || :OLD.Race_Name || ', Date: ' || TO_CHAR(:OLD.Race_Date, 'YYYY-MM-DD');
        END IF;
        
        INSERT INTO Audit_Log (
            Audit_ID,
            Action_Type,
            Table_Name,
            Record_ID,
            Action_Details,
            Action_By
        ) VALUES (
            SYS_GUID(),
            v_action,
            'Race',
            v_record_id,
            v_details,
            USER
        );
    END;
    """
    
    create_season_trigger = """
    CREATE OR REPLACE TRIGGER audit_season_trigger
    AFTER INSERT OR UPDATE OR DELETE ON Season
    FOR EACH ROW
    DECLARE
        v_action VARCHAR2(10);
        v_record_id VARCHAR2(100);
        v_details VARCHAR2(1000);
    BEGIN
        IF INSERTING THEN
            v_action := 'INSERT';
            v_record_id := :NEW.Year;
            v_details := 'Team Winner: ' || :NEW.Team_Winner || 
                        ', Individual Winner: ' || :NEW.Individual_Winner;
        ELSIF UPDATING THEN
            v_action := 'UPDATE';
            v_record_id := :NEW.Year;
            v_details := 'Old Team Winner: ' || :OLD.Team_Winner || 
                        ', New Team Winner: ' || :NEW.Team_Winner ||
                        ', Old Individual Winner: ' || :OLD.Individual_Winner || 
                        ', New Individual Winner: ' || :NEW.Individual_Winner;
        ELSE
            v_action := 'DELETE';
            v_record_id := :OLD.Year;
            v_details := 'Team Winner: ' || :OLD.Team_Winner || 
                        ', Individual Winner: ' || :OLD.Individual_Winner;
        END IF;
        
        INSERT INTO Audit_Log (
            Audit_ID,
            Action_Type,
            Table_Name,
            Record_ID,
            Action_Details,
            Action_By
        ) VALUES (
            SYS_GUID(),
            v_action,
            'Season',
            v_record_id,
            v_details,
            USER
        );
    END;
    """
    
    try:
        # Create audit table
        # try:
        #     execute_query("DROP TABLE Audit_Log", fetch=False)
        # except:
        #     pass
        # execute_query(create_table, fetch=False)
        
        # Create triggers
        execute_query(create_trigger, fetch=False)
        execute_query(create_driver_trigger, fetch=False)
        execute_query(create_race_trigger, fetch=False)
        execute_query(create_season_trigger, fetch=False)
    except cx_Oracle.Error as error:
        print(f"Audit setup error: {error}")

# Complex Queries for the application
def get_team_standings(year=None):
    if not year:
        year = get_current_season()
    query = """
    SELECT t.Team_Name, t.Team_Score, t.Principal_First_Name || ' ' || t.Principal_Last_Name as Principal,
           COUNT(DISTINCT d.Driver_ID) AS Drivers_Count
    FROM Team t
    LEFT JOIN Driver d ON t.Team_Name = d.Team_Name AND t.Year = d.Year
    WHERE t.Year = :year
    GROUP BY t.Team_Name, t.Team_Score, t.Principal_First_Name, t.Principal_Last_Name
    ORDER BY t.Team_Score DESC
    """
    return execute_query(query, {'year': year})

def get_driver_standings(year=None):
    if not year:
        year = get_current_season()
    query = """
    SELECT d.Driver_ID, d.First_Name || ' ' || d.Last_Name AS Driver_Name, 
           d.Total_Ind_Score, d.Team_Name, d.Nationality
    FROM Driver d
    WHERE d.Year = :year
    ORDER BY d.Total_Ind_Score DESC
    """
    return execute_query(query, {'year': year})

def get_race_schedule(year=None):
    if not year:
        year = get_current_season()
    query = """
    SELECT r.Race_Name, TO_CHAR(r.Race_Date, 'YYYY-MM-DD') as Race_Date, 
           r.Country, r.State, r.Circuit_Name,
           c.Circuit_Length
    FROM Race r
    JOIN Circuit c ON r.Circuit_Name = c.Circuit_Name
    WHERE r.Year = :year
    ORDER BY r.Race_Date
    """
    return execute_query(query, {'year': year})

def get_championship_history(limit=8):
    query = """
    SELECT s.Year, s.Team_Winner, s.Individual_Winner,
           (SELECT MAX(Team_Score) FROM Team WHERE Year = s.Year) AS Winning_Team_Score,
           (SELECT MAX(Total_Ind_Score) FROM Driver WHERE Year = s.Year) AS Winning_Driver_Score
    FROM Season s
    ORDER BY s.Year DESC
    """
    if limit:
        query += f" FETCH FIRST {limit} ROWS ONLY"
    return execute_query(query)

def get_driver_details(driver_id):
    query = """
    SELECT d.Driver_ID, d.First_Name, d.Last_Name, d.Nationality, 
           d.Total_Ind_Score, d.Team_Name, d.Year,
           (SELECT COUNT(*) FROM Result r WHERE r.Driver_ID = d.Driver_ID AND r.Position = 1) AS Wins,
           (SELECT COUNT(*) FROM Result r WHERE r.Driver_ID = d.Driver_ID AND r.Position <= 3) AS Podiums
    FROM Driver d
    WHERE d.Driver_ID = :driver_id
    """
    return execute_query(query, {'driver_id': driver_id})

def get_team_details(team_name, year=None):
    if not year:
        year = get_current_season()
    query = """
    SELECT t.Team_Name, t.Principal_First_Name, t.Principal_Last_Name, 
           t.Team_Score, t.Year,
           (SELECT COUNT(*) FROM Driver d WHERE d.Team_Name = t.Team_Name AND d.Year = t.Year) AS Driver_Count,
           (SELECT COUNT(*) FROM Result r WHERE r.Team_Name = t.Team_Name AND r.Year = t.Year AND r.Position = 1) AS Team_Wins
    FROM Team t
    WHERE t.Team_Name = :team_name AND t.Year = :year
    """
    return execute_query(query, {'team_name': team_name, 'year': year})

def get_circuit_races(circuit_name):
    query = """
    SELECT r.Race_Name, TO_CHAR(r.Race_Date, 'YYYY-MM-DD') as Race_Date, r.Year,
           (SELECT s.Team_Winner FROM Season s WHERE s.Year = r.Year) AS Season_Winner
    FROM Race r
    WHERE r.Circuit_Name = :circuit_name
    ORDER BY r.Year DESC, r.Race_Date DESC
    """
    return execute_query(query, {'circuit_name': circuit_name})

def get_driver_results(driver_id, year=None):
    if not year:
        year = get_current_season()
    query = """
    SELECT res.Result_ID, res.Position, res.Points,
           r.Race_Name, TO_CHAR(r.Race_Date, 'YYYY-MM-DD') as Race_Date
    FROM Result res
    JOIN Race r ON res.Result_ID LIKE '%' || r.Race_Name || '%' AND r.Year = res.Year
    WHERE res.Driver_ID = :driver_id AND res.Year = :year
    ORDER BY r.Race_Date
    """
    return execute_query(query, {'driver_id': driver_id, 'year': year})

def get_top_performing_drivers_by_nationality(nationality=None, limit=10):
    query = """
    SELECT d.First_Name || ' ' || d.Last_Name AS Driver_Name, 
           d.Nationality, SUM(d.Total_Ind_Score) AS Total_Career_Points,
           COUNT(DISTINCT d.Year) AS Seasons_Competed
    FROM Driver d
    """
    if nationality:
        query += " WHERE d.Nationality = :nationality"
        params = {'nationality': nationality}
    else:
        params = None
    query += """
    GROUP BY d.First_Name, d.Last_Name, d.Nationality
    ORDER BY Total_Career_Points DESC
    """
    if limit:
        query += f" FETCH FIRST {limit} ROWS ONLY"
    return execute_query(query, params)

def get_race_sessions_for_race(race_name):
    query = """
    SELECT rs.Race_SessionID, rs.Duration, rs.Changed_Duration
    FROM RaceSession rs
    WHERE rs.Race_Name = :race_name
    """
    return execute_query(query, {'race_name': race_name})

def get_audit_log():
    query = """
    SELECT Audit_ID, Action_Type, Table_Name, Record_ID, Action_Details, 
           Action_By, TO_CHAR(Action_Date, 'YYYY-MM-DD HH24:MI:SS') as Action_Date
    FROM Audit_Log
    ORDER BY Action_Date DESC
    """
    return execute_query(query)

def get_current_season():
    query = "SELECT MAX(Year) FROM Season"
    result = execute_query(query)
    return result[0][0] if result else datetime.now().year

def get_driver_position_function(driver_id, race_name):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # First, drop the existing function if it's invalid
        try:
            cursor.execute("DROP FUNCTION get_driver_position")
            conn.commit()
        except:
            pass
        
        # Create a valid function that properly handles your Result_ID format
        create_function = """
        CREATE OR REPLACE FUNCTION get_driver_position(
            p_driver_id IN NUMBER,
            p_race_name IN VARCHAR2
        ) RETURN NUMBER IS
            v_position NUMBER;
        BEGIN
            SELECT Position INTO v_position
            FROM Result
            WHERE Driver_ID = p_driver_id 
            AND SUBSTR(Result_ID, 1, INSTR(Result_ID, '-') - 1) = p_race_name;
            
            RETURN v_position;
        EXCEPTION
            WHEN NO_DATA_FOUND THEN
                RETURN NULL;
            WHEN OTHERS THEN
                RETURN -1;
        END;
        """
        cursor.execute(create_function)
        conn.commit()
        
        # Now call the function with the properly formatted parameters
        # First check if function exists and is valid
        check_query = """
        SELECT STATUS FROM USER_OBJECTS 
        WHERE OBJECT_TYPE = 'FUNCTION' AND OBJECT_NAME = 'GET_DRIVER_POSITION'
        """
        cursor.execute(check_query)
        status = cursor.fetchone()
        
        if status and status[0] == 'VALID':
            cursor.execute(
                "SELECT get_driver_position(:driver_id, :race_name) FROM DUAL", 
                {'driver_id': driver_id, 'race_name': race_name}
            )
            result = cursor.fetchone()
            return result[0] if result else None
        else:
            print("Function is still invalid after recreation")
            check_function_errors()
            return None
        
    except cx_Oracle.Error as error:
        print(f"PL/SQL Function Error: {error}")
        return None
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# def update_team_score_procedure(team_name, year, additional_points):
#     """Update a team's score directly with SQL"""
#     conn = None
#     cursor = None
#     try:
#         conn = get_db_connection()
#         cursor = conn.cursor()
        
#         # First, check which schema we're actually using
#         cursor.execute("SELECT USER FROM DUAL")
#         current_user = cursor.fetchone()[0]
#         print(f"Connected as user: {current_user}")
        
#         # Instead of using a procedure, do a direct update
#         update_sql = """
#         UPDATE Team
#         SET Team_Score = NVL(Team_Score, 0) + :points
#         WHERE Team_Name = :team AND Year = :year
#         """
        
#         cursor.execute(update_sql, {
#             'points': additional_points,
#             'team': team_name,
#             'year': year
#         })
        
#         rows_updated = cursor.rowcount
#         conn.commit()
        
#         if rows_updated > 0:
#             return True
#         else:
#             print(f"No team found with name '{team_name}' for year {year}")
#             return False
        
#     except cx_Oracle.Error as error:
#         print(f"Database Error: {error}")
#         return False
#     finally:
#         if cursor:
#             cursor.close()
#         if conn:
#             conn.close()

def update_team_score_procedure(team_name, year, additional_points):
    """Create and call a PL/SQL procedure to update a team's score"""
    import cx_Oracle
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Create the PL/SQL procedure if it doesn't exist
        plsql = """
        CREATE OR REPLACE PROCEDURE update_team_score_proc (
            p_team_name        IN VARCHAR2,
            p_year             IN NUMBER,
            p_additional_points IN NUMBER
        )
        AS
        BEGIN
            UPDATE Team
            SET Team_Score = NVL(Team_Score, 0) + p_additional_points
            WHERE Team_Name = p_team_name AND Year = p_year;

            IF SQL%ROWCOUNT = 0 THEN
                DBMS_OUTPUT.PUT_LINE('No team found with name "' || p_team_name || '" for year ' || p_year);
            ELSE
                DBMS_OUTPUT.PUT_LINE('Team score updated successfully.');
            END IF;

            COMMIT;
        EXCEPTION
            WHEN OTHERS THEN
                DBMS_OUTPUT.PUT_LINE('Error: ' || SQLERRM);
                ROLLBACK;
        END;
        """
        cursor.execute(plsql)

        # Call the procedure
        cursor.callproc("update_team_score_proc", [team_name, year, additional_points])
        conn.commit()
        print("Procedure created and executed successfully.")
        return True

    except cx_Oracle.Error as error:
        print(f"Database Error: {error}")
        return False
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def get_race_session_count_function(race_name):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        check_function = """
        SELECT COUNT(*) FROM USER_OBJECTS 
        WHERE OBJECT_TYPE = 'FUNCTION' AND OBJECT_NAME = 'GET_RACE_SESSION_COUNT'
        """
        cursor.execute(check_function)
        exists = cursor.fetchone()[0]
        if not exists:
            create_function = """
            CREATE OR REPLACE FUNCTION get_race_session_count(
                p_race_name IN VARCHAR2
            ) RETURN NUMBER IS
                v_count NUMBER;
            BEGIN
                SELECT COUNT(*) INTO v_count
                FROM RaceSession
                WHERE Race_Name = p_race_name;
                RETURN v_count;
            EXCEPTION
                WHEN NO_DATA_FOUND THEN
                    RETURN 0;
            END;
            """
            cursor.execute(create_function)
            conn.commit()
        cursor.execute("SELECT get_race_session_count(:race_name) FROM DUAL", 
                      {'race_name': race_name})
        result = cursor.fetchone()
        return result[0] if result else 0
    except cx_Oracle.Error as error:
        print(f"PL/SQL Function Error: {error}")
        return None
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# GUI Class for the application
class RaceManagementApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Race Data Management System")
        self.root.geometry("1200x700")
        
        # Theme configurations
        self.themes = {
            'light': {
                'background': '#f0f0f0',
                'foreground': '#000000',
                'header_bg': '#f0f0f0',
                'header_fg': '#000000',
                'button_bg': '#e0e0e0',
                'button_fg': '#000000',
                'treeview_bg': '#ffffff',
                'treeview_fg': '#000000',
                'entry_bg': '#ffffff',
                'entry_fg': '#000000',
                'combobox_bg': '#ffffff',
                'combobox_fg': '#000000'
            },
            # 'dark': {
            #     'background': '#2b2b2b',
            #     'foreground': '#ffffff',
            #     'header_bg': '#2b2b2b',
            #     'header_fg': '#ffffff',
            #     'button_bg': '#ffffff',
            #     'button_fg': '#000000',
            #     'treeview_bg': '#ffffff',
            #     'treeview_fg': '#000000',
            #     'entry_bg': '#ffffff',
            #     'entry_fg': '#000000',
            #     'combobox_bg': '#ffffff',
            #     'combobox_fg': '#000000'
            'dark': {
                'background': '#1e1e1e',      # Main window background
                'foreground': '#e0e0e0',      # Default text color
                'header_bg': '#2c2c2c',       # Header background
                'header_fg': '#f5f5f5',       # Header text color
                'button_bg': '#3a3a3a',       # Dark button background
                'button_fg': '#494949',       # White button text
                'treeview_bg': '#252526',     # Treeview background
                'treeview_fg': '#e0e0e0',     # Treeview text color
                'entry_bg': '#2d2d2d',        # Entry/Combobox background
                'entry_fg': '#494949',        # Entry/Combobox text color
                'combobox_bg': '#2d2d2d',     # Same as entry
                'combobox_fg': '#494949'
            }
        }
        self.current_theme = 'light'
        
        # Set up styles
        self.style = ttk.Style()
        self.apply_theme(self.current_theme)
        
        # Create main container
        self.main_frame = ttk.Frame(self.root)
        self.main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Create login frame first
        self.create_login_frame()
        
        # Initialize application state variables
        self.current_user = None
        self.is_admin = False
        
        # Setup audit log
        if self.is_admin:
            setup_audit_log()
        
        # Start Flask API server in a separate thread
        self.start_flask_server()

    def apply_theme(self, theme_name):
        """Apply the specified theme to the UI"""
        theme = self.themes[theme_name]
        self.style.configure('TFrame', background=theme['background'])
        self.style.configure('TLabel', background=theme['background'], foreground=theme['foreground'], font=('Arial', 10))
        self.style.configure('TButton', background=theme['button_bg'], foreground=theme['button_fg'], font=('Arial', 10))
        self.style.configure('Header.TLabel', background=theme['header_bg'], foreground=theme['header_fg'], font=('Arial', 16, 'bold'))
        self.style.configure('Treeview', background=theme['treeview_bg'], foreground=theme['treeview_fg'], fieldbackground=theme['treeview_bg'])
        self.style.configure('Treeview.Heading', background=theme['button_bg'], foreground=theme['button_fg'])
        self.style.configure('TEntry', fieldbackground=theme['entry_bg'], foreground=theme['entry_fg'])
        self.style.configure('TCombobox', fieldbackground=theme['combobox_bg'], foreground=theme['combobox_fg'])
        
        # Update existing widgets if they exist
        if hasattr(self, 'main_frame'):
            for widget in self.main_frame.winfo_children():
                self.update_widget_theme(widget, theme_name)

    def update_widget_theme(self, widget, theme_name):
        """Recursively update widget styles for theme change"""
        theme = self.themes[theme_name]
        if isinstance(widget, ttk.Frame):
            widget.configure(style='TFrame')
        elif isinstance(widget, ttk.Label):
            widget.configure(style='TLabel' if 'Header' not in widget.cget('style') else 'Header.TLabel')
        elif isinstance(widget, ttk.Button):
            widget.configure(style='TButton')
        elif isinstance(widget, ttk.Treeview):
            widget.configure(style='Treeview')
        elif isinstance(widget, ttk.Entry):
            widget.configure(style='TEntry')
        elif isinstance(widget, ttk.Combobox):
            widget.configure(style='TCombobox')
        
        for child in widget.winfo_children():
            self.update_widget_theme(child, theme_name)

    def toggle_theme(self):
        """Toggle between light and dark themes"""
        self.current_theme = 'dark' if self.current_theme == 'light' else 'light'
        self.apply_theme(self.current_theme)
        # Update toggle button text
        if hasattr(self, 'theme_btn'):
            self.theme_btn.configure(text=f"Switch to {'Dark' if self.current_theme == 'light' else 'Light'} Mode")

    def start_flask_server(self):
        global flask_thread
        @app.route('/api/teams', methods=['GET'])
        def get_teams_api():
            year = request.args.get('year', default=get_current_season(), type=int)
            try:
                teams = get_team_standings(year)
                result = [
                    {
                        "team_name": team[0],
                        "team_score": team[1],
                        "principal": team[2],
                        "drivers_count": team[3]
                    }
                    for team in teams
                ]
                return jsonify({"success": True, "teams": result})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500
        
        @app.route('/api/drivers', methods=['GET'])
        def get_drivers_api():
            year = request.args.get('year', default=get_current_season(), type=int)
            try:
                drivers = get_driver_standings(year)
                result = [
                    {
                        "driver_id": driver[0],
                        "driver_name": driver[1],
                        "total_score": driver[2],
                        "team_name": driver[3],
                        "nationality": driver[4]
                    }
                    for driver in drivers
                ]
                return jsonify({"success": True, "drivers": result})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500
        
        @app.route('/api/races', methods=['GET'])
        def get_races_api():
            year = request.args.get('year', default=get_current_season(), type=int)
            try:
                races = get_race_schedule(year)
                result = [
                    {
                        "race_name": race[0],
                        "race_date": race[1],
                        "country": race[2],
                        "state": race[3],
                        "circuit_name": race[4],
                        "circuit_length": float(race[5])
                    }
                    for race in races
                ]
                return jsonify({"success": True, "races": result})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500
        
        def run_flask():
            app.run(host='127.0.0.1', port=5000, debug=False)
        
        flask_thread = threading.Thread(target=run_flask)
        flask_thread.daemon = True
        flask_thread.start()

    def create_login_frame(self):
        for widget in self.main_frame.winfo_children():
            widget.destroy()
        login_frame = ttk.Frame(self.main_frame, padding="30")
        login_frame.place(relx=0.5, rely=0.5, anchor="center")
        header_label = ttk.Label(login_frame, text="Race Data Management System", style='Header.TLabel')
        header_label.grid(row=0, column=0, columnspan=2, pady=(0, 20))
        guest_btn = ttk.Button(login_frame, text="Login as Guest", width=20,
                              command=lambda: self.login_user("Guest", False))
        guest_btn.grid(row=1, column=0, padx=10, pady=10)
        admin_btn = ttk.Button(login_frame, text="Login as Admin", width=20,
                              command=self.admin_login_dialog)
        admin_btn.grid(row=1, column=1, padx=10, pady=10)
        exit_btn = ttk.Button(login_frame, text="Exit", width=20,
                             command=self.exit_application)
        exit_btn.grid(row=2, column=0, columnspan=2, pady=(20, 0))

    def admin_login_dialog(self):
        password = simpledialog.askstring("Admin Login", "Enter admin password:", show='*')
        if password == "admin123":
            self.login_user("Admin", True)
            setup_audit_log()  # Setup audit log when admin logs in
        else:
            messagebox.showerror("Login Failed", "Incorrect admin password")

    def login_user(self, username, is_admin):
        self.current_user = username
        self.is_admin = is_admin
        messagebox.showinfo("Login Successful", f"Welcome, {username}!")
        self.create_main_interface()

    def exit_application(self):
        if messagebox.askyesno("Exit", "Are you sure you want to exit?"):
            self.root.quit()

    def create_main_interface(self):
        for widget in self.main_frame.winfo_children():
            widget.destroy()
        top_frame = ttk.Frame(self.main_frame)
        top_frame.pack(fill=tk.X, padx=10, pady=5)
        header_text = f"Race Data Management System - Logged in as {self.current_user}"
        header_label = ttk.Label(top_frame, text=header_text, style='Header.TLabel')
        header_label.pack(side=tk.LEFT, padx=10)
        logout_btn = ttk.Button(top_frame, text="Logout", command=self.create_login_frame)
        logout_btn.pack(side=tk.RIGHT, padx=10)
        self.theme_btn = ttk.Button(top_frame, text="Switch to Dark Mode", command=self.toggle_theme)
        self.theme_btn.pack(side=tk.RIGHT, padx=10)
        nav_frame = ttk.Frame(self.main_frame)
        nav_frame.pack(fill=tk.X, padx=10, pady=5)
        team_btn = ttk.Button(nav_frame, text="Team Standings", 
                             command=lambda: self.show_team_standings())
        team_btn.pack(side=tk.LEFT, padx=5)
        driver_btn = ttk.Button(nav_frame, text="Driver Standings", 
                               command=lambda: self.show_driver_standings())
        driver_btn.pack(side=tk.LEFT, padx=5)
        race_btn = ttk.Button(nav_frame, text="Race Schedule", 
                             command=lambda: self.show_race_schedule())
        race_btn.pack(side=tk.LEFT, padx=5)
        history_btn = ttk.Button(nav_frame, text="Championship History", 
                                command=lambda: self.show_championship_history())
        history_btn.pack(side=tk.LEFT, padx=5)
        admin_btn = ttk.Button(nav_frame, text="Run Complex Queries", 
                              command=lambda: self.show_complex_queries())
        admin_btn.pack(side=tk.LEFT, padx=5)
        if self.is_admin:
            audit_btn = ttk.Button(nav_frame, text="Audit Log", 
                                  command=lambda: self.show_audit_log())
            audit_btn.pack(side=tk.LEFT, padx=5)
        self.content_frame = ttk.Frame(self.main_frame)
        self.content_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.show_team_standings()

    def clear_content_frame(self):
        for widget in self.content_frame.winfo_children():
            widget.destroy()

    def show_audit_log(self):
        self.clear_content_frame()
        header_label = ttk.Label(self.content_frame, text="Audit Log", style='Header.TLabel')
        header_label.pack(anchor=tk.W, pady=(0, 10))
        columns = ("ID", "Action", "Table", "Record ID", "Details", "User", "Date")
        tree = ttk.Treeview(self.content_frame, columns=columns, show="headings")
        for col in columns:
            tree.heading(col, text=col)
        tree.column("ID", width=100)
        tree.column("Action", width=80)
        tree.column("Table", width=100)
        tree.column("Record ID", width=150)
        tree.column("Details", width=400)
        tree.column("User", width=100)
        tree.column("Date", width=150)
        scrollbar = ttk.Scrollbar(self.content_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(fill=tk.BOTH, expand=True)
        try:
            audit_logs = get_audit_log()
            for log in audit_logs:
                tree.insert("", "end", values=(log[0], log[1], log[2], log[3], log[4], log[5], log[6]))
        except cx_Oracle.Error as error:
            messagebox.showerror("Database Error", str(error))
        if self.is_admin:
            admin_frame = ttk.Frame(self.content_frame)
            admin_frame.pack(fill=tk.X, pady=(10, 0))
            refresh_btn = ttk.Button(admin_frame, text="Refresh", 
                                   command=self.show_audit_log)
            refresh_btn.pack(side=tk.LEFT, padx=5)

    def show_team_standings(self, year=None):
        self.clear_content_frame()
        if not year:
            year = get_current_season()
        header_frame = ttk.Frame(self.content_frame)
        header_frame.pack(fill=tk.X, pady=(0, 10))
        header_label = ttk.Label(header_frame, text=f"Team Standings - {year}", style='Header.TLabel')
        header_label.pack(side=tk.LEFT)
        year_frame = ttk.Frame(header_frame)
        year_frame.pack(side=tk.RIGHT)
        ttk.Label(year_frame, text="Select Year:").pack(side=tk.LEFT, padx=(0, 5))
        years = execute_query("SELECT DISTINCT Year FROM Season ORDER BY Year DESC")
        year_var = tk.StringVar(value=str(year))
        year_combo = ttk.Combobox(year_frame, textvariable=year_var, 
                                 values=[str(y[0]) for y in years], width=6)
        year_combo.pack(side=tk.LEFT)
        year_btn = ttk.Button(year_frame, text="Go", 
                             command=lambda: self.show_team_standings(int(year_var.get())))
        year_btn.pack(side=tk.LEFT, padx=(5, 0))
        columns = ("Position", "Team", "Principal", "Points", "Drivers")
        tree = ttk.Treeview(self.content_frame, columns=columns, show="headings")
        for col in columns:
            tree.heading(col, text=col)
        tree.column("Position", width=80, anchor=tk.CENTER)
        tree.column("Team", width=200)
        tree.column("Principal", width=200)
        tree.column("Points", width=80, anchor=tk.CENTER)
        tree.column("Drivers", width=80, anchor=tk.CENTER)
        scrollbar = ttk.Scrollbar(self.content_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(fill=tk.BOTH, expand=True)
        teams = get_team_standings(year)
        for position, team in enumerate(teams, 1):
            tree.insert("", "end", values=(position, team[0], team[2], team[1], team[3]))
        if self.is_admin:
            admin_frame = ttk.Frame(self.content_frame)
            admin_frame.pack(fill=tk.X, pady=(10, 0))
            add_btn = ttk.Button(admin_frame, text="Add Team", 
                               command=lambda: self.team_edit_dialog())
            add_btn.pack(side=tk.LEFT, padx=5)
            # edit_btn = ttk.Button(admin_frame, text="Edit Team", 
            #                     command=lambda: self.team_edit_dialog(tree.item(tree.selection())["values"][1] if tree.selection() else None))
            # edit_btn.pack(side=tk.LEFT, padx=5)
            delete_btn = ttk.Button(admin_frame, text="Delete Team", 
                                  command=lambda: self.delete_team(tree.item(tree.selection())["values"][1] if tree.selection() else None))
            delete_btn.pack(side=tk.LEFT, padx=5)

    def show_driver_standings(self, year=None):
        self.clear_content_frame()
        if not year:
            year = get_current_season()
        header_frame = ttk.Frame(self.content_frame)
        header_frame.pack(fill=tk.X, pady=(0, 10))
        header_label = ttk.Label(header_frame, text=f"Driver Standings - {year}", style='Header.TLabel')
        header_label.pack(side=tk.LEFT)
        year_frame = ttk.Frame(header_frame)
        year_frame.pack(side=tk.RIGHT)
        ttk.Label(year_frame, text="Select Year:").pack(side=tk.LEFT, padx=(0, 5))
        years = execute_query("SELECT DISTINCT Year FROM Season ORDER BY Year DESC")
        year_var = tk.StringVar(value=str(year))
        year_combo = ttk.Combobox(year_frame, textvariable=year_var, 
                                 values=[str(y[0]) for y in years], width=6)
        year_combo.pack(side=tk.LEFT)
        year_btn = ttk.Button(year_frame, text="Go", 
                             command=lambda: self.show_driver_standings(int(year_var.get())))
        year_btn.pack(side=tk.LEFT, padx=(5, 0))
        columns = ("Position", "Driver", "Team", "Nationality", "Points")
        tree = ttk.Treeview(self.content_frame, columns=columns, show="headings")
        for col in columns:
            tree.heading(col, text=col)
        tree.column("Position", width=80, anchor=tk.CENTER)
        tree.column("Driver", width=200)
        tree.column("Team", width=200)
        tree.column("Nationality", width=150)
        tree.column("Points", width=80, anchor=tk.CENTER)
        scrollbar = ttk.Scrollbar(self.content_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(fill=tk.BOTH, expand=True)
        drivers = get_driver_standings(year)
        for position, driver in enumerate(drivers, 1):
            tree.insert("", "end", values=(position, driver[1], driver[3], driver[4], driver[2]))
        if self.is_admin:
            admin_frame = ttk.Frame(self.content_frame)
            admin_frame.pack(fill=tk.X, pady=(10, 0))
            add_btn = ttk.Button(admin_frame, text="Add Driver", 
                               command=lambda: self.driver_edit_dialog())
            add_btn.pack(side=tk.LEFT, padx=5)
            # edit_btn = ttk.Button(admin_frame, text="Edit Driver", 
            #                     command=lambda: self.driver_edit_dialog(
            #                         [d for d in drivers if d[1] == tree.item(tree.selection())["values"][1]][0][0] 
            #                         if tree.selection() else None))
            # edit_btn.pack(side=tk.LEFT, padx=5)
            delete_btn = ttk.Button(admin_frame, text="Delete Driver", 
                                  command=lambda: self.delete_driver(
                                      [d for d in drivers if d[1] == tree.item(tree.selection())["values"][1]][0][0] 
                                      if tree.selection() else None))
            delete_btn.pack(side=tk.LEFT, padx=5)

    def show_race_schedule(self, year=None):
        self.clear_content_frame()
        if not year:
            year = get_current_season()
        header_frame = ttk.Frame(self.content_frame)
        header_frame.pack(fill=tk.X, pady=(0, 10))
        header_label = ttk.Label(header_frame, text=f"Race Schedule - {year}", style='Header.TLabel')
        header_label.pack(side=tk.LEFT)
        year_frame = ttk.Frame(header_frame)
        year_frame.pack(side=tk.RIGHT)
        ttk.Label(year_frame, text="Select Year:").pack(side=tk.LEFT, padx=(0, 5))
        years = execute_query("SELECT DISTINCT Year FROM Season ORDER BY Year DESC")
        year_var = tk.StringVar(value=str(year))
        year_combo = ttk.Combobox(year_frame, textvariable=year_var, 
                                 values=[str(y[0]) for y in years], width=6)
        year_combo.pack(side=tk.LEFT)
        year_btn = ttk.Button(year_frame, text="Go", 
                             command=lambda: self.show_race_schedule(int(year_var.get())))
        year_btn.pack(side=tk.LEFT, padx=(5, 0))
        columns = ("Race", "Date", "Circuit", "Location", "Circuit Length")
        tree = ttk.Treeview(self.content_frame, columns=columns, show="headings")
        for col in columns:
            tree.heading(col, text=col)
        tree.column("Race", width=200)
        tree.column("Date", width=100)
        tree.column("Circuit", width=200)
        tree.column("Location", width=200)
        tree.column("Circuit Length", width=100, anchor=tk.CENTER)
        scrollbar = ttk.Scrollbar(self.content_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(fill=tk.BOTH, expand=True)
        races = get_race_schedule(year)
        for race in races:
            location = f"{race[2]}, {race[3]}" if race[3] else race[2]
            tree.insert("", "end", values=(race[0], race[1], race[4], location, f"{race[5]} km"))
        if self.is_admin:
            admin_frame = ttk.Frame(self.content_frame)
            admin_frame.pack(fill=tk.X, pady=(10, 0))
            add_btn = ttk.Button(admin_frame, text="Add Race", 
                              command=lambda: self.race_edit_dialog())
            add_btn.pack(side=tk.LEFT, padx=5)
            # edit_btn = ttk.Button(admin_frame, text="Edit Race", 
            #                     command=lambda: self.race_edit_dialog(
            #                         tree.item(tree.selection())["values"][0] if tree.selection() else None))
            # edit_btn.pack(side=tk.LEFT, padx=5)
            delete_btn = ttk.Button(admin_frame, text="Delete Race", 
                                  command=lambda: self.delete_race(
                                      tree.item(tree.selection())["values"][0] if tree.selection() else None))
            delete_btn.pack(side=tk.LEFT, padx=5)
            session_btn = ttk.Button(admin_frame, text="View Race Sessions", 
                                   command=lambda: self.show_race_sessions(
                                       tree.item(tree.selection())["values"][0] if tree.selection() else None))
            session_btn.pack(side=tk.LEFT, padx=5)

    def show_championship_history(self):
        self.clear_content_frame()
        header_label = ttk.Label(self.content_frame, text="Championship History", style='Header.TLabel')
        header_label.pack(anchor=tk.W, pady=(0, 10))
        columns = ("Year", "Team Champion", "Driver Champion", "Team Points", "Driver Points")
        tree = ttk.Treeview(self.content_frame, columns=columns, show="headings")
        for col in columns:
            tree.heading(col, text=col)
        tree.column("Year", width=80, anchor=tk.CENTER)
        tree.column("Team Champion", width=200)
        tree.column("Driver Champion", width=200)
        tree.column("Team Points", width=100, anchor=tk.CENTER)
        tree.column("Driver Points", width=100, anchor=tk.CENTER)
        scrollbar = ttk.Scrollbar(self.content_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(fill=tk.BOTH, expand=True)
        championships = get_championship_history()
        for championship in championships:
            tree.insert("", "end", values=(championship[0], championship[1], championship[2], 
                                          championship[3], championship[4]))
        if self.is_admin:
            admin_frame = ttk.Frame(self.content_frame)
            admin_frame.pack(fill=tk.X, pady=(10, 0))
            add_btn = ttk.Button(admin_frame, text="Add Season", 
                               command=lambda: self.season_edit_dialog())
            add_btn.pack(side=tk.LEFT, padx=5)
            edit_btn = ttk.Button(admin_frame, text="Edit Season", 
                                command=lambda: self.season_edit_dialog(
                                    tree.item(tree.selection())["values"][0] if tree.selection() else None))
            edit_btn.pack(side=tk.LEFT, padx=5)
            delete_btn = ttk.Button(admin_frame, text="Delete Season", 
                                  command=lambda: self.delete_season(
                                      tree.item(tree.selection())["values"][0] if tree.selection() else None))
            delete_btn.pack(side=tk.LEFT, padx=5)

    def show_race_sessions(self, race_name):
        if not race_name:
            messagebox.showerror("Error", "No race selected")
            return
        count = get_race_session_count_function(race_name)
        sessions = get_race_sessions_for_race(race_name)
        dialog = tk.Toplevel(self.root)
        dialog.title(f"Sessions for {race_name}")
        dialog.geometry("400x300")
        dialog.transient(self.root)
        dialog.grab_set()
        ttk.Label(dialog, text=f"Total Sessions: {count}", font=('Arial', 12)).pack(pady=10)
        tree = ttk.Treeview(dialog, columns=("Session ID", "Duration", "Changed Duration"), show="headings")
        tree.heading("Session ID", text="Session ID")
        tree.heading("Duration", text="Duration")
        tree.heading("Changed Duration", text="Changed Duration")
        tree.pack(fill=tk.BOTH, expand=True)
        for session in sessions:
            tree.insert("", "end", values=(session[0], session[1], session[2]))
        ttk.Button(dialog, text="Close", command=dialog.destroy).pack(pady=10)

    def show_complex_queries(self):
        self.clear_content_frame()
        header_label = ttk.Label(self.content_frame, text="Complex Queries", style='Header.TLabel')
        header_label.pack(anchor=tk.W, pady=(0, 10))
        year_frame = ttk.Frame(self.content_frame)
        year_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(year_frame, text="Select Year:").pack(side=tk.LEFT, padx=(0, 5))
        years = execute_query("SELECT DISTINCT Year FROM Season ORDER BY Year DESC")
        self.year_var = tk.StringVar(value=str(years[0][0]) if years else "2025")
        year_combo = ttk.Combobox(year_frame, textvariable=self.year_var, 
                                values=[str(y[0]) for y in years], width=6)
        year_combo.pack(side=tk.LEFT)
        queries = [
            ("Top Teams by Points", self.run_query_top_teams),
            ("Top Drivers by Points", self.run_query_top_drivers),
            ("Multiple Championships", self.run_query_multiple_champions),
            ("Popular Circuits", self.run_query_popular_circuits),
            ("Average Team Scores", lambda: self.run_query_avg_team_scores(int(self.year_var.get()))),
            ("Driver Improvements", self.run_query_driver_improvements),
            ("Longest Race Sessions", self.run_query_longest_sessions),
            ("Nationality Driver Count", self.run_query_nationality_count),
            ("PL/SQL: Driver Position", self.run_query_driver_position),
            #("PL/SQL: Update Team Score", None),
            ("PL/SQL: Race Session Count", self.run_query_race_session_count)
        ]

       # if self.is_guest:
           # queries.insert(-1, ("PL/SQL: Update Team Score(LOCKED)", None))

        if self.is_admin:
            queries.insert(-1, ("PL/SQL: Update Team Score", self.run_query_update_team_score))

        for name, command in queries:
            ttk.Button(self.content_frame, text=name, command=command).pack(fill=tk.X, padx=50, pady=5)
        ttk.Button(self.content_frame, text="Back", command=self.create_main_interface).pack(pady=10)

    def run_query_top_teams(self):
        year = self.year_var.get()
        query = f"""
        SELECT t.Team_Name, t.Team_Score
        FROM Team t
        WHERE t.Year = {year}
        ORDER BY t.Team_Score DESC
        FETCH FIRST 5 ROWS ONLY
        """
        self.display_query_results(execute_query(query), ["Team Name", "Total Points"])

    def run_query_top_drivers(self):
        year = self.year_var.get()
        query = f"""
        SELECT d.First_Name || ' ' || d.Last_Name as Driver_Name, d.Total_Ind_Score
        FROM Driver d
        WHERE d.Year = {year}
        ORDER BY d.Total_Ind_Score DESC
        FETCH FIRST 5 ROWS ONLY
        """
        self.display_query_results(execute_query(query), ["Driver Name", "Total Points"])

    def run_query_multiple_champions(self):
        query = """
        SELECT s.Individual_Winner, COUNT(*) as championships
        FROM Season s
        GROUP BY s.Individual_Winner
        HAVING COUNT(*) > 1
        ORDER BY championships DESC
        """
        self.display_query_results(execute_query(query), ["Driver Name", "Championships"])

    def run_query_popular_circuits(self):
        year = self.year_var.get()
        query = f"""
        SELECT c.Circuit_Name, COUNT(r.Race_Name) as race_count
        FROM Circuit c
        JOIN Race r ON r.Circuit_Name = c.Circuit_Name
        WHERE r.Year = {year}
        GROUP BY c.Circuit_Name
        ORDER BY race_count DESC
        FETCH FIRST 5 ROWS ONLY
        """
        self.display_query_results(execute_query(query), ["Circuit Name", "Race Count"])

    def run_query_avg_team_scores(self, year):
        query = f"""
        SELECT t.Team_Name, AVG(t.Team_Score) as avg_score
        FROM Team t
        WHERE t.Year = {year}
        GROUP BY t.Team_Name
        ORDER BY avg_score DESC
        """
        self.display_query_results(execute_query(query), ["Team Name", "Average Score"])

    def run_query_driver_improvements(self):
        query = """
        SELECT d.First_Name || ' ' || d.Last_Name as Driver_Name, 
               (d.Total_Ind_Score - LAG(d.Total_Ind_Score) OVER (PARTITION BY d.First_Name, d.Last_Name ORDER BY d.Year)) as score_diff
        FROM Driver d
        WHERE d.Year IN (SELECT Year FROM Season)
        ORDER BY score_diff DESC NULLS LAST
        FETCH FIRST 5 ROWS ONLY
        """
        self.display_query_results(execute_query(query), ["Driver Name", "Score Improvement"])

    def run_query_longest_sessions(self):
        year = self.year_var.get()
        query = f"""
        SELECT r.Race_Name, MAX(rs.Duration) as max_duration
        FROM Race r
        JOIN RaceSession rs ON rs.Race_Name = r.Race_Name
        WHERE r.Year = {year}
        GROUP BY r.Race_Name
        ORDER BY max_duration DESC
        FETCH FIRST 5 ROWS ONLY
        """
        self.display_query_results(execute_query(query), ["Race Name", "Max Duration"])

    def run_query_nationality_count(self):
        year = self.year_var.get()
        query = f"""
        SELECT d.Nationality, COUNT(*) as driver_count
        FROM Driver d
        WHERE d.Year = {year}
        GROUP BY d.Nationality
        ORDER BY driver_count DESC
        """
        self.display_query_results(execute_query(query), ["Nationality", "Driver Count"])

    def run_query_driver_position(self):
        driver_id = simpledialog.askinteger("Input", "Enter Driver ID:", minvalue=1)
        race_name = simpledialog.askstring("Input", "Enter Race Name:")
        if driver_id and race_name:
            position = get_driver_position_function(driver_id, race_name)
            result = [(f"Driver ID {driver_id} in {race_name}", position if position is not None else "Not found")]
            self.display_query_results(result, ["Query", "Position"])

    def run_query_update_team_score(self):
        team_name = simpledialog.askstring("Input", "Enter Team Name:")
        year = simpledialog.askinteger("Input", "Enter Year:", minvalue=2000)
        points = simpledialog.askinteger("Input", "Enter Additional Points:", minvalue=0)
        
        if team_name and year and points is not None:
            success = update_team_score_procedure(team_name, year, points)
            result = [(f"Update {team_name} ({year})", "Success" if success else "Failed")]
            self.display_query_results(result, ["Query", "Status"])

    def run_query_race_session_count(self):
        race_name = simpledialog.askstring("Input", "Enter Race Name:")
        if race_name:
            count = get_race_session_count_function(race_name)
            result = [(f"Sessions for {race_name}", count if count is not None else "Error")]
            self.display_query_results(result, ["Query", "Count"])

    def display_query_results(self, results, headers):
        self.clear_content_frame()
        tree = ttk.Treeview(self.content_frame, columns=headers, show="headings")
        for header in headers:
            tree.heading(header, text=header)
        tree.pack(fill=tk.BOTH, expand=True)
        for result in results:
            tree.insert("", "end", values=result)
        ttk.Button(self.content_frame, text="Back", command=self.show_complex_queries).pack(pady=10)

    def team_edit_dialog(self, team_name=None):
        is_edit = team_name is not None
        dialog = tk.Toplevel(self.root)
        dialog.title(f"{'Edit' if is_edit else 'Add'} Team")
        dialog.geometry("400x300")
        dialog.transient(self.root)
        dialog.grab_set()
        years = execute_query("SELECT DISTINCT Year FROM Season ORDER BY Year DESC")
        year_values = [str(y[0]) for y in years]
        form_frame = ttk.Frame(dialog, padding="20")
        form_frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(form_frame, text="Team Name:").grid(row=0, column=0, sticky=tk.W, pady=5)
        name_var = tk.StringVar()
        name_entry = ttk.Entry(form_frame, textvariable=name_var, width=30)
        name_entry.grid(row=0, column=1, sticky=tk.W, pady=5)
        ttk.Label(form_frame, text="Principal First Name:").grid(row=1, column=0, sticky=tk.W, pady=5)
        first_name_var = tk.StringVar()
        first_name_entry = ttk.Entry(form_frame, textvariable=first_name_var, width=30)
        first_name_entry.grid(row=1, column=1, sticky=tk.W, pady=5)
        ttk.Label(form_frame, text="Principal Last Name:").grid(row=2, column=0, sticky=tk.W, pady=5)
        last_name_var = tk.StringVar()
        last_name_entry = ttk.Entry(form_frame, textvariable=last_name_var, width=30)
        last_name_entry.grid(row=2, column=1, sticky=tk.W, pady=5)
        ttk.Label(form_frame, text="Team Score:").grid(row=3, column=0, sticky=tk.W, pady=5)
        score_var = tk.StringVar()
        score_entry = ttk.Entry(form_frame, textvariable=score_var, width=30)
        score_entry.grid(row=3, column=1, sticky=tk.W, pady=5)
        ttk.Label(form_frame, text="Year:").grid(row=4, column=0, sticky=tk.W, pady=5)
        year_var = tk.StringVar(value=str(get_current_season()))
        year_combo = ttk.Combobox(form_frame, textvariable=year_var, values=year_values, width=28)
        year_combo.grid(row=4, column=1, sticky=tk.W, pady=5)
        if is_edit:
            team_data = get_team_details(team_name)
            if team_data:
                name_var.set(team_data[0][0])
                first_name_var.set(team_data[0][1])
                last_name_var.set(team_data[0][2])
                score_var.set(str(team_data[0][3]))
                year_var.set(str(team_data[0][4]))
                name_entry.configure(state="disabled")
                year_combo.configure(state="disabled")
        btn_frame = ttk.Frame(form_frame)
        btn_frame.grid(row=5, column=0, columnspan=2, pady=(20, 0))
        save_btn = ttk.Button(btn_frame, text="Save", width=15,
                             command=lambda: self.save_team(
                                 name_var.get(), first_name_var.get(), last_name_var.get(),
                                 score_var.get(), year_var.get(), dialog, is_edit))
        save_btn.pack(side=tk.LEFT, padx=5)
        cancel_btn = ttk.Button(btn_frame, text="Cancel", width=15,
                               command=dialog.destroy)
        cancel_btn.pack(side=tk.LEFT, padx=5)

    def save_team(self, team_name, first_name, last_name, score, year, dialog, is_edit):
        try:
            if not team_name or not first_name or not last_name or not score or not year:
                messagebox.showerror("Error", "All fields are required")
                return
            try:
                score = int(score)
                year = int(year)
            except ValueError:
                messagebox.showerror("Error", "Score and Year must be numbers")
                return
            if is_edit:
                query = """
                UPDATE Team 
                SET Principal_First_Name = :first_name, 
                    Principal_Last_Name = :last_name, 
                    Team_Score = :score
                WHERE Team_Name = :team_name AND Year = :year
                """
            else:
                query = """
                INSERT INTO Team (Team_Name, Principal_First_Name, Principal_Last_Name, Team_Score, Year)
                VALUES (:team_name, :first_name, :last_name, :score, :year)
                """
            execute_query(query, {
                'team_name': team_name, 
                'first_name': first_name, 
                'last_name': last_name, 
                'score': score, 
                'year': year
            }, fetch=False)
            messagebox.showinfo("Success", f"Team {team_name} {'updated' if is_edit else 'added'} successfully")
            dialog.destroy()
            self.show_team_standings(year)
        except cx_Oracle.Error as error:
            messagebox.showerror("Database Error", str(error))

    def delete_team(self, team_name):
        if not team_name:
            messagebox.showerror("Error", "No team selected")
            return
        if not messagebox.askyesno("Confirm Delete", f"Are you sure you want to delete team {team_name}?"):
            return
        try:
            year = get_current_season()
            query = "DELETE FROM Team WHERE Team_Name = :team_name AND Year = :year"
            execute_query(query, {'team_name': team_name, 'year': year}, fetch=False)
            messagebox.showinfo("Success", f"Team {team_name} deleted successfully")
            self.show_team_standings()
        except cx_Oracle.Error as error:
            messagebox.showerror("Database Error", str(error))

    def driver_edit_dialog(self, driver_id=None):
        is_edit = driver_id is not None
        dialog = tk.Toplevel(self.root)
        dialog.title(f"{'Edit' if is_edit else 'Add'} Driver")
        dialog.geometry("400x400")
        dialog.transient(self.root)
        dialog.grab_set()
        year = get_current_season()
        teams = execute_query("SELECT Team_Name FROM Team WHERE Year = :year", {'year': year})
        team_values = [t[0] for t in teams]
        form_frame = ttk.Frame(dialog, padding="20")
        form_frame.pack(fill=tk.BOTH, expand=True)
        if not is_edit:
            ttk.Label(form_frame, text="Driver ID:").grid(row=0, column=0, sticky=tk.W, pady=5)
            id_var = tk.StringVar()
            id_entry = ttk.Entry(form_frame, textvariable=id_var, width=30)
            id_entry.grid(row=0, column=1, sticky=tk.W, pady=5)
            row_offset = 0
        else:
            id_var = tk.StringVar(value=str(driver_id))
            row_offset = -1
        ttk.Label(form_frame, text="First Name:").grid(row=1+row_offset, column=0, sticky=tk.W, pady=5)
        first_name_var = tk.StringVar()
        first_name_entry = ttk.Entry(form_frame, textvariable=first_name_var, width=30)
        first_name_entry.grid(row=1+row_offset, column=1, sticky=tk.W, pady=5)
        ttk.Label(form_frame, text="Last Name:").grid(row=2+row_offset, column=0, sticky=tk.W, pady=5)
        last_name_var = tk.StringVar()
        last_name_entry = ttk.Entry(form_frame, textvariable=last_name_var, width=30)
        last_name_entry.grid(row=2+row_offset, column=1, sticky=tk.W, pady=5)
        ttk.Label(form_frame, text="Nationality:").grid(row=3+row_offset, column=0, sticky=tk.W, pady=5)
        nationality_var = tk.StringVar()
        nationality_entry = ttk.Entry(form_frame, textvariable=nationality_var, width=30)
        nationality_entry.grid(row=3+row_offset, column=1, sticky=tk.W, pady=5)
        ttk.Label(form_frame, text="Total Score:").grid(row=4+row_offset, column=0, sticky=tk.W, pady=5)
        score_var = tk.StringVar(value="0")
        score_entry = ttk.Entry(form_frame, textvariable=score_var, width=30)
        score_entry.grid(row=4+row_offset, column=1, sticky=tk.W, pady=5)
        ttk.Label(form_frame, text="Team:").grid(row=5+row_offset, column=0, sticky=tk.W, pady=5)
        team_var = tk.StringVar()
        team_combo = ttk.Combobox(form_frame, textvariable=team_var, values=team_values, width=28)
        team_combo.grid(row=5+row_offset, column=1, sticky=tk.W, pady=5)
        if is_edit:
            driver_data = get_driver_details(driver_id)
            if driver_data:
                first_name_var.set(driver_data[0][1])
                last_name_var.set(driver_data[0][2])
                nationality_var.set(driver_data[0][3])
                score_var.set(str(driver_data[0][4]))
                team_var.set(driver_data[0][5])
        btn_frame = ttk.Frame(form_frame)
        btn_frame.grid(row=6+row_offset, column=0, columnspan=2, pady=(20, 0))
        save_btn = ttk.Button(btn_frame, text="Save", width=15,
                             command=lambda: self.save_driver(
                                 id_var.get(), first_name_var.get(), last_name_var.get(),
                                 nationality_var.get(), score_var.get(), team_var.get(), 
                                 dialog, is_edit))
        save_btn.pack(side=tk.LEFT, padx=5)
        cancel_btn = ttk.Button(btn_frame, text="Cancel", width=15,
                               command=dialog.destroy)
        cancel_btn.pack(side=tk.LEFT, padx=5)

    def save_driver(self, driver_id, first_name, last_name, nationality, score, team, dialog, is_edit):
        try:
            if not driver_id or not first_name or not last_name or not nationality or not score or not team:
                messagebox.showerror("Error", "All fields are required")
                return
            try:
                driver_id = int(driver_id)
                score = int(score)
            except ValueError:
                messagebox.showerror("Error", "Driver ID and Score must be numbers")
                return
            year = get_current_season()
            if is_edit:
                query = """
                UPDATE Driver 
                SET First_Name = :first_name, 
                    Last_Name = :last_name, 
                    Nationality = :nationality,
                    Total_Ind_Score = :score,
                    Team_Name = :team
                WHERE Driver_ID = :driver_id
                """
            else:
                query = """
                INSERT INTO Driver (Driver_ID, First_Name, Last_Name, Nationality, Total_Ind_Score, Team_Name, Year)
                VALUES (:driver_id, :first_name, :last_name, :nationality, :score, :team, :year)
                """
            execute_query(query, {
                'driver_id': driver_id, 
                'first_name': first_name, 
                'last_name': last_name, 
                'nationality': nationality,
                'score': score, 
                'team': team,
                'year': year
            }, fetch=False)
            messagebox.showinfo("Success", f"Driver {first_name} {last_name} {'updated' if is_edit else 'added'} successfully")
            dialog.destroy()
            self.show_driver_standings()
        except cx_Oracle.Error as error:
            messagebox.showerror("Database Error", str(error))

    def delete_driver(self, driver_id):
        if not driver_id:
            messagebox.showerror("Error", "No driver selected")
            return
        if not messagebox.askyesno("Confirm Delete", f"Are you sure you want to delete driver ID {driver_id}?"):
            return
        try:
            query = "DELETE FROM Driver WHERE Driver_ID = :driver_id"
            execute_query(query, {'driver_id': driver_id}, fetch=False)
            messagebox.showinfo("Success", f"Driver ID {driver_id} deleted successfully")
            self.show_driver_standings()
        except cx_Oracle.Error as error:
            messagebox.showerror("Database Error", str(error))

    def race_edit_dialog(self, race_name=None):
        is_edit = race_name is not None
        dialog = tk.Toplevel(self.root)
        dialog.title(f"{'Edit' if is_edit else 'Add'} Race")
        dialog.geometry("400x400")
        dialog.transient(self.root)
        dialog.grab_set()
        countries = execute_query("SELECT DISTINCT Country FROM Location")
        country_values = [c[0] for c in countries]
        circuits = execute_query("SELECT Circuit_Name FROM Circuit")
        circuit_values = [c[0] for c in circuits]
        years = execute_query("SELECT DISTINCT Year FROM Season ORDER BY Year DESC")
        year_values = [str(y[0]) for y in years]
        form_frame = ttk.Frame(dialog, padding="20")
        form_frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(form_frame, text="Race Name:").grid(row=0, column=0, sticky=tk.W, pady=5)
        name_var = tk.StringVar()
        name_entry = ttk.Entry(form_frame, textvariable=name_var, width=30)
        name_entry.grid(row=0, column=1, sticky=tk.W, pady=5)
        ttk.Label(form_frame, text="Race Date (YYYY-MM-DD):").grid(row=1, column=0, sticky=tk.W, pady=5)
        date_var = tk.StringVar()
        date_entry = ttk.Entry(form_frame, textvariable=date_var, width=30)
        date_entry.grid(row=1, column=1, sticky=tk.W, pady=5)
        ttk.Label(form_frame, text="Country:").grid(row=2, column=0, sticky=tk.W, pady=5)
        country_var = tk.StringVar()
        country_combo = ttk.Combobox(form_frame, textvariable=country_var, values=country_values, width=28)
        country_combo.grid(row=2, column=1, sticky=tk.W, pady=5)
        ttk.Label(form_frame, text="State:").grid(row=3, column=0, sticky=tk.W, pady=5)
        state_var = tk.StringVar()
        state_entry = ttk.Entry(form_frame, textvariable=state_var, width=30)
        state_entry.grid(row=3, column=1, sticky=tk.W, pady=5)
        ttk.Label(form_frame, text="Circuit:").grid(row=4, column=0, sticky=tk.W, pady=5)
        circuit_var = tk.StringVar()
        circuit_combo = ttk.Combobox(form_frame, textvariable=circuit_var, values=circuit_values, width=28)
        circuit_combo.grid(row=4, column=1, sticky=tk.W, pady=5)
        ttk.Label(form_frame, text="Year:").grid(row=5, column=0, sticky=tk.W, pady=5)
        year_var = tk.StringVar(value=str(get_current_season()))
        year_combo = ttk.Combobox(form_frame, textvariable=year_var, values=year_values, width=28)
        year_combo.grid(row=5, column=1, sticky=tk.W, pady=5)
        if is_edit:
            race_data = execute_query("""
                SELECT Race_Name, TO_CHAR(Race_Date, 'YYYY-MM-DD'), Country, State, Circuit_Name, Year 
                FROM Race WHERE Race_Name = :race_name
            """, {'race_name': race_name})
            if race_data:
                name_var.set(race_data[0][0])
                date_var.set(race_data[0][1])
                country_var.set(race_data[0][2])
                state_var.set(race_data[0][3])
                circuit_var.set(race_data[0][4])
                year_var.set(str(race_data[0][5]))
                name_entry.configure(state="disabled")
        btn_frame = ttk.Frame(form_frame)
        btn_frame.grid(row=6, column=0, columnspan=2, pady=(20, 0))
        save_btn = ttk.Button(btn_frame, text="Save", width=15,
                             command=lambda: self.save_race(
                                 name_var.get(), date_var.get(), country_var.get(),
                                 state_var.get(), circuit_var.get(), year_var.get(), 
                                 dialog, is_edit))
        save_btn.pack(side=tk.LEFT, padx=5)
        cancel_btn = ttk.Button(btn_frame, text="Cancel", width=15,
                               command=dialog.destroy)
        cancel_btn.pack(side=tk.LEFT, padx=5)

    def save_race(self, race_name, race_date, country, state, circuit, year, dialog, is_edit):
        try:
            if not race_name or not race_date or not country or not circuit or not year:
                messagebox.showerror("Error", "Required fields: Race Name, Date, Country, Circuit, Year")
                return
            try:
                year = int(year)
                datetime.strptime(race_date, '%Y-%m-%d')
            except ValueError:
                messagebox.showerror("Error", "Year must be a number and Date must be in YYYY-MM-DD format")
                return
            if is_edit:
                query = """
                UPDATE Race 
                SET Race_Date = TO_DATE(:race_date, 'YYYY-MM-DD'), 
                    Country = :country, 
                    State = :state,
                    Circuit_Name = :circuit,
                    Year = :year
                WHERE Race_Name = :race_name
                """
            else:
                query = """
                INSERT INTO Race (Race_Name, Race_Date, Country, State, Circuit_Name, Year)
                VALUES (:race_name, TO_DATE(:race_date, 'YYYY-MM-DD'), :country, :state, :circuit, :year)
                """
            execute_query(query, {
                'race_name': race_name, 
                'race_date': race_date, 
                'country': country, 
                'state': state,
                'circuit': circuit, 
                'year': year
            }, fetch=False)
            messagebox.showinfo("Success", f"Race {race_name} {'updated' if is_edit else 'added'} successfully")
            dialog.destroy()
            self.show_race_schedule(year)
        except cx_Oracle.Error as error:
            messagebox.showerror("Database Error", str(error))

    def delete_race(self, race_name):
        if not race_name:
            messagebox.showerror("Error", "No race selected")
            return
        if not messagebox.askyesno("Confirm Delete", f"Are you sure you want to delete race {race_name}?"):
            return
        try:
            query = "DELETE FROM Race WHERE Race_Name = :race_name"
            execute_query(query, {'race_name': race_name}, fetch=False)
            messagebox.showinfo("Success", f"Race {race_name} deleted successfully")
            self.show_race_schedule()
        except cx_Oracle.Error as error:
            messagebox.showerror("Database Error", str(error))

    def season_edit_dialog(self, year=None):
        is_edit = year is not None
        dialog = tk.Toplevel(self.root)
        dialog.title(f"{'Edit' if is_edit else 'Add'} Season")
        dialog.geometry("400x250")
        dialog.transient(self.root)
        dialog.grab_set()
        form_frame = ttk.Frame(dialog, padding="20")
        form_frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(form_frame, text="Year:").grid(row=0, column=0, sticky=tk.W, pady=5)
        year_var = tk.StringVar()
        year_entry = ttk.Entry(form_frame, textvariable=year_var, width=30)
        year_entry.grid(row=0, column=1, sticky=tk.W, pady=5)
        ttk.Label(form_frame, text="Team Winner:").grid(row=1, column=0, sticky=tk.W, pady=5)
        team_var = tk.StringVar()
        team_entry = ttk.Entry(form_frame, textvariable=team_var, width=30)
        team_entry.grid(row=1, column=1, sticky=tk.W, pady=5)
        ttk.Label(form_frame, text="Individual Winner:").grid(row=2, column=0, sticky=tk.W, pady=5)
        individual_var = tk.StringVar()
        individual_entry = ttk.Entry(form_frame, textvariable=individual_var, width=30)
        individual_entry.grid(row=2, column=1, sticky=tk.W, pady=5)
        if is_edit:
            season_data = execute_query("SELECT Year, Team_Winner, Individual_Winner FROM Season WHERE Year = :year", 
                                       {'year': year})
            if season_data:
                year_var.set(str(season_data[0][0]))
                team_var.set(season_data[0][1])
                individual_var.set(season_data[0][2])
                year_entry.configure(state="disabled")
        btn_frame = ttk.Frame(form_frame)
        btn_frame.grid(row=3, column=0, columnspan=2, pady=(20, 0))
        save_btn = ttk.Button(btn_frame, text="Save", width=15,
                             command=lambda: self.save_season(
                                 year_var.get(), team_var.get(), individual_var.get(), 
                                 dialog, is_edit))
        save_btn.pack(side=tk.LEFT, padx=5)
        cancel_btn = ttk.Button(btn_frame, text="Cancel", width=15,
                               command=dialog.destroy)
        cancel_btn.pack(side=tk.LEFT, padx=5)

    def save_season(self, year, team_winner, individual_winner, dialog, is_edit):
        try:
            if not year or not team_winner or not individual_winner:
                messagebox.showerror("Error", "All fields are required")
                return
            try:
                year = int(year)
            except ValueError:
                messagebox.showerror("Error", "Year must be a number")
                return
            if is_edit:
                query = """
                UPDATE Season 
                SET Team_Winner = :team_winner, 
                    Individual_Winner = :individual_winner
                WHERE Year = :year
                """
            else:
                query = """
                INSERT INTO Season (Year, Team_Winner, Individual_Winner)
                VALUES (:year, :team_winner, :individual_winner)
                """
            execute_query(query, {
                'year': year, 
                'team_winner': team_winner, 
                'individual_winner': individual_winner
            }, fetch=False)
            messagebox.showinfo("Success", f"Season {year} {'updated' if is_edit else 'added'} successfully")
            dialog.destroy()
            self.show_championship_history()
        except cx_Oracle.Error as error:
            messagebox.showerror("Database Error", str(error))

    def delete_season(self, year):
        if not year:
            messagebox.showerror("Error", "No season selected")
            return
        if not messagebox.askyesno("Confirm Delete", f"Are you sure you want to delete season {year}?"):
            return
        try:
            query = "DELETE FROM Season WHERE Year = :year"
            execute_query(query, {'year': year}, fetch=False)
            messagebox.showinfo("Success", f"Season {year} deleted successfully")
            self.show_championship_history()
        except cx_Oracle.Error as error:
            messagebox.showerror("Database Error", str(error))

if __name__ == "__main__":
    try:
        try:
            cx_Oracle.version
        except cx_Oracle.DatabaseError as e:
            messagebox.showerror("Error", "Oracle client not properly configured. Please install Oracle Instant Client.")
            sys.exit(1)
        root = tk.Tk()
        app = RaceManagementApp(root)
        root.mainloop()
    except Exception as e:
        messagebox.showerror("Startup Error", f"Failed to start application: {str(e)}")
        sys.exit(1)
