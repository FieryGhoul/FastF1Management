DROP TABLE RaceSession CASCADE CONSTRAINTS;
DROP TABLE Result CASCADE CONSTRAINTS;
DROP TABLE Race CASCADE CONSTRAINTS;
DROP TABLE Driver CASCADE CONSTRAINTS;
DROP TABLE Team CASCADE CONSTRAINTS;
DROP TABLE Circuit CASCADE CONSTRAINTS;
DROP TABLE Location CASCADE CONSTRAINTS;
DROP TABLE Season CASCADE CONSTRAINTS;
DROP TABLE Audit_Log CASCADE CONSTRAINTS;


CREATE TABLE Season (
    Year int PRIMARY KEY,
    Team_Winner varchar(255),
    Individual_Winner varchar(255)
);

CREATE TABLE Location (
    Country varchar(255),
    State varchar(255),
    PRIMARY KEY (Country, State)
);

CREATE TABLE Circuit (
    Circuit_Name varchar(255) PRIMARY KEY,
    Circuit_Length decimal(10,2)
);

CREATE TABLE Team (
    Team_Name varchar(255),
    Principal_First_Name varchar(255),
    Principal_Last_Name varchar(255),
    Team_Score int,
    Year int,
    PRIMARY KEY (Team_Name, Year),
    FOREIGN KEY (Year) REFERENCES Season (Year)
);

CREATE TABLE Driver (
  Driver_ID        NUMBER        NOT NULL,
  Nationality      VARCHAR2(255),
  Last_Name        VARCHAR2(255),
  First_Name       VARCHAR2(255),
  Total_Ind_Score  NUMBER,
  Year             NUMBER        NOT NULL,
  Team_Name        VARCHAR2(255),
  PRIMARY KEY (Driver_ID, Year),
  FOREIGN KEY (Year)               REFERENCES Season (Year),
  FOREIGN KEY (Team_Name, Year)    REFERENCES Team   (Team_Name, Year)
);

CREATE TABLE Race (
    Race_Name varchar(255) PRIMARY KEY,
    Race_Date date,
    Country varchar(255),
    State varchar(255),
    Circuit_Name varchar(255),
    Year int,
    FOREIGN KEY (Circuit_Name) REFERENCES Circuit (Circuit_Name),
    FOREIGN KEY (Year) REFERENCES Season (Year),
    FOREIGN KEY (Country, State) REFERENCES Location (Country, State)
);

CREATE TABLE Result (
  Result_ID   VARCHAR2(255) PRIMARY KEY,
  Position    NUMBER        NOT NULL,
  Points      NUMBER,
  Driver_ID   NUMBER        NOT NULL,
  Team_Name   VARCHAR2(255),
  Year        NUMBER        NOT NULL,
  FOREIGN KEY (Year)
    REFERENCES Season (Year),
  FOREIGN KEY (Driver_ID, Year)
    REFERENCES Driver (Driver_ID, Year),
  FOREIGN KEY (Team_Name, Year)
    REFERENCES Team   (Team_Name, Year)
);


CREATE TABLE RaceSession (
    Duration int,
    Race_SessionID varchar(255) PRIMARY KEY,
    Race_Name varchar(255),
    Changed_Duration int,
    FOREIGN KEY (Race_Name) REFERENCES Race (Race_Name)
);

CREATE TABLE Audit_Log (
        Audit_ID VARCHAR2(36),
        Action_Type VARCHAR2(10),
        Table_Name VARCHAR2(50),
        Record_ID VARCHAR2(100),
        Action_Details VARCHAR2(1000),
        Action_By VARCHAR2(50),
        Action_Date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (Audit_ID)
);
