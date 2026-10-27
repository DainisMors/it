forward
global type n_cst_usage_tracker from nonvisualobject
end type
end forward

global type n_cst_usage_tracker from nonvisualobject
event ue_savetofile ( )
end type
global n_cst_usage_tracker n_cst_usage_tracker

type variables
n_cst_usage_registry		inv_Registry

Protected:
string    				is_SessionId, is_UserId, is_AppVersion
datetime  			idt_SessionStart
long      			il_StartCpu, il_EvCount = 0, il_MaxEvents = 20000
boolean   			ib_Enabled = FALSE, ib_Overflow = FALSE
string    				is_MachineName
boolean   			ib_Recovering = FALSE       // TRUE während of_recover_temp (kein erneutes Auslagern)

n_cst_platform	inv_Platform      // PFC Plattform-Service
s_usage_event	istr_Ev[]

end variables

forward prototypes
public function integer of_start_session (string as_userid, string as_appversion)
public function integer of_track (string as_window, string as_control, string as_type, string as_group)
protected function integer of_track_raw (string as_window, string as_control, string as_type, string as_descr, string as_group)
public function integer of_track_window (w_master aw_win)
public function integer of_end_session (string as_reason)
protected function string of_get_tempfile ()
protected function string of_clean (string as_src)
protected function long of_split (string as_line, ref string as_f[])
protected function datetime of_str_to_dt (string as_val)
protected function integer of_save_temp (string as_reason, datetime adt_end, long al_dursec)
public function long of_recover_temp ()
end prototypes

event ue_savetofile();string						ls_File
n_cst_impexp		lnv_IE
//n_cst_platform	lnv_Platform

//	f_SetPlatform( lnv_Platform, TRUE )
//	f_SetPlatform( lnv_Platform, FALSE )


	lnv_IE = CREATE n_cst_impexp
	ls_File = f_AddBackSlash( lnv_IE.of_GetProfiling()) + "UKAUsage.json"
	FileDelete( ls_File)
	

RETURN 
end event

public function integer of_start_session (string as_userid, string as_appversion);string  	ls_Machine
long    	ll_Cfg

is_UserId = as_UserId
is_AppVersion = as_AppVersion
idt_SessionStart = DateTime(Today(), Now())
il_StartCpu = Cpu()

// --- 1. Konfiguration laden (DataStore) ---
inv_Registry = CREATE n_cst_usage_registry
ll_Cfg = inv_Registry.of_Load()

IF ll_Cfg <= 0 THEN
    // Kein Element konfiguriert — Erfassung sinnlos, still deaktivieren
    ib_Enabled = FALSE
    RETURN 0
END IF

// --- 2. Session-ID von SQL Anywhere generieren lassen ---
SELECT CAST(NEWID() AS CHAR(36))
INTO :is_SessionId
FROM SYS.DUMMY;

IF SQLCA.SQLCode <> 0 OR IsNull(is_SessionId) OR Len(is_SessionId) = 0 THEN
    ib_Enabled = FALSE
    RETURN -1
END IF

// --- 3. Rechnername über PFC n_cst_platform ---
f_SetPlatform( inv_Platform, TRUE )
ls_Machine = inv_Platform.of_GetComputerName()
f_SetPlatform( inv_Platform, FALSE )

IF IsNull(ls_Machine) THEN ls_Machine = ""
is_MachineName = ls_Machine

// --- 4. Session-Kopfzeile sofort schreiben (CRASH als Vorgabe) ---
INSERT INTO org.tracking_session (session_id, user_id, machine_name, app_version, session_start, close_reason)
VALUES (:is_SessionId, :is_UserId, :ls_Machine, :is_AppVersion, :idt_SessionStart, 'CRASH');

IF SQLCA.SQLCode <> 0 THEN
    ib_Enabled = FALSE
    RETURN -1
END IF
COMMIT;

ib_Enabled = TRUE

RETURN ll_Cfg

end function

public function integer of_track (string as_window, string as_control, string as_type, string as_group);string		ls_Descr

IF NOT ib_Enabled THEN 
	RETURN 0
END IF

//clipboard(as_Window + ", " + as_Control + ", " + as_Type + ", " + ls_Descr + ", " + as_Group)
//messagebox("//dm", as_Window + ", " + as_Control + ", " + as_Type + ", " + ls_Descr + ", " + as_Group )

IF NOT inv_Registry.of_Lookup(as_Window, as_Control, as_Type, ls_Descr, as_Group) THEN
    RETURN 0
END IF

RETURN of_Track_Raw(as_Window, as_Control, as_Type, ls_Descr, as_Group)

end function

protected function integer of_track_raw (string as_window, string as_control, string as_type, string as_descr, string as_group);IF il_EvCount >= il_MaxEvents THEN
    ib_overflow = TRUE
    RETURN 0
END IF

il_EvCount ++
istr_ev[il_EvCount].window_name  = as_Window
istr_ev[il_EvCount].control_name = as_Control
istr_ev[il_EvCount].control_type = as_Type
istr_ev[il_EvCount].descr = as_Descr
istr_ev[il_EvCount].func_group = as_Group
istr_ev[il_EvCount].elapsed_ms = Cpu() - il_StartCpu
istr_ev[il_EvCount].stamp = DateTime(Today(), Now())

RETURN 1

end function

public function integer of_track_window (w_master aw_win);//clipboard(aw_Win.ClassName())
//messagebox("//dm", "of_track_window, " + aw_Win.ClassName())

IF NOT ib_Enabled OR NOT IsValid(aw_Win) THEN 
	RETURN 0
END IF

RETURN of_Track(aw_Win.ClassName(), "*", "window", "")
end function

public function integer of_end_session (string as_reason);long     		ll_Counter, ll_Counter2, ll_Uniq = 0, ll_DurSec, ll_Seq = 0, ll_Acnt[]
string   		ls_Aw[], ls_Ac[], ls_At[], ls_Ad[], ls_Ag[]
datetime 	ldt_End, ldt_afirst[], ldt_alast[]
boolean  	lb_Found


IF NOT ib_Enabled THEN 
	RETURN 0
END IF 

ib_Enabled = FALSE      // ab hier keine Ereignisse mehr annehmen
ldt_End = DateTime(Today(), Now())
ll_DurSec = Round((Cpu() - il_StartCpu)/1000,0)

// ===================== 1. AGGREGATION =====================
FOR ll_Counter = 1 TO il_EvCount
	lb_Found = FALSE
   FOR ll_Counter2 = 1 TO ll_Uniq
      IF ls_Aw[ll_Counter2] = istr_Ev[ll_Counter].window_name  AND &
      		ls_Ac[ll_Counter2] = istr_Ev[ll_Counter].control_name AND &
				ls_At[ll_Counter2] = istr_Ev[ll_Counter].control_type THEN
         ll_Acnt[ll_Counter2] ++
         ldt_alast[ll_Counter2] = istr_Ev[ll_Counter].stamp
         lb_Found = TRUE
         EXIT
      END IF
   NEXT

   IF NOT lb_found THEN
   	ll_Uniq ++
      ls_Aw[ll_Uniq] = istr_Ev[ll_Counter].window_name
      ls_Ac[ll_Uniq] = istr_Ev[ll_Counter].control_name
      ls_At[ll_Uniq] = istr_Ev[ll_Counter].control_type
      ls_Ad[ll_Uniq] = istr_Ev[ll_Counter].descr
      ls_Ag[ll_Uniq] = istr_Ev[ll_Counter].func_group
      ll_Acnt[ll_Uniq] = 1
      ldt_afirst[ll_Uniq] = istr_Ev[ll_Counter].stamp
      ldt_alast[ll_Uniq] = istr_Ev[ll_Counter].stamp
   END IF
NEXT

// ===================== 2. SUMMARISCH → DB =====================
string   		ls_w, ls_c, ls_t, ls_d, ls_g
long     		ll_cnt
datetime 	ldt_f, ldt_l

FOR ll_Counter2 = 1 TO ll_Uniq
    ls_w = ls_Aw[ll_Counter2]
	 ls_c = ls_Ac[ll_Counter2]
	 ls_t = ls_At[ll_Counter2]
    ls_d = ls_Ad[ll_Counter2]
	 ls_g = ls_Ag[ll_Counter2]
    ll_cnt = ll_Acnt[ll_Counter2]
	 ldt_f = ldt_afirst[ll_Counter2]
	 ldt_l = ldt_alast[ll_Counter2]

   INSERT INTO org.tracking_summary (session_id, window_name, control_name, control_type, descr, func_group, action_count, first_at, last_at)
    VALUES (:is_SessionId, :ls_w, :ls_c, :ls_t, :ls_d, :ls_g, :ll_cnt, :ldt_f, :ldt_l);

    IF SQLCA.SQLCode < 0 THEN
        ROLLBACK USING SQLCA;
        RETURN of_save_temp(as_reason, ldt_End, ll_DurSec)
    END IF
NEXT

// ===================== 3. REIHENFOLGE (im Speicher komprimiert) =====================
string   		ls_sw[], ls_sc[], ls_st[], ls_sd[]
longlong 	ll_sms[]
long     		ll_srep[]
string   		ls_key, ls_prevkey = "~~none~~"

FOR ll_Counter = 1 TO il_EvCount
    ls_key = istr_Ev[ll_Counter].window_name + "|" + istr_Ev[ll_Counter].control_name + "|" + istr_Ev[ll_Counter].control_type

    IF ls_key = ls_prevkey THEN
        ll_srep[ll_seq] ++              // aufeinanderfolgende gleiche Klicks → ein Eintrag
    ELSE
        ll_seq ++
        ls_sw[ll_seq] = istr_Ev[ll_Counter].window_name
        ls_sc[ll_seq] = istr_Ev[ll_Counter].control_name
        ls_st[ll_seq] = istr_Ev[ll_Counter].control_type
        ls_sd[ll_seq] = istr_Ev[ll_Counter].descr
        ll_sms[ll_seq] = istr_Ev[ll_Counter].elapsed_ms
        ll_srep[ll_seq] = 1
        ls_prevkey = ls_key
    END IF
NEXT

longlong ll_ms
FOR ll_Counter2 = 1 TO ll_seq
    ls_w = ls_sw[ll_Counter2]; ls_c = ls_sc[ll_Counter2]; ls_t = ls_st[ll_Counter2]
    ls_d = ls_sd[ll_Counter2]; ll_ms = ll_sms[ll_Counter2]; ll_cnt = ll_srep[ll_Counter2]

    INSERT INTO org.tracking_sequence (session_id, seq_no, elapsed_ms, window_name, control_name, control_type, descr, repeat_count)
    VALUES (:is_SessionId, :ll_Counter2, :ll_ms, :ls_w, :ls_c, :ls_t, :ls_d, :ll_cnt);

    IF SQLCA.SQLCode < 0 THEN
        ROLLBACK USING SQLCA;
        RETURN of_save_temp(as_reason, ldt_End, ll_DurSec)
    END IF
NEXT

// ===================== 4. SESSION-KOPFZEILE =====================
string 		ls_reason
ls_reason = as_reason
IF ib_overflow THEN ls_reason = as_reason + "/OVF"

UPDATE org.tracking_session
SET session_end   = :ldt_end,
      duration_sec  = :ll_DurSec,
      total_actions = :il_EvCount,
      close_reason  = :ls_reason
WHERE session_id = :is_SessionId;

IF SQLCA.SQLCode < 0 THEN
    ROLLBACK USING SQLCA;
    RETURN of_save_temp(as_reason, ldt_End, ll_DurSec)
END IF

COMMIT USING SQLCA;

IF SQLCA.SQLCode < 0 THEN
    ROLLBACK USING SQLCA;
    RETURN of_save_temp(as_reason, ldt_End, ll_DurSec)
END IF

RETURN ll_Uniq

end function

protected function string of_get_tempfile ();ContextKeyword	lcxk_Base
string					ls_V[]

GetContextService("Keyword", lcxk_Base)
lcxk_Base.GetContextKeywords("TEMP", ls_V)
IF UpperBound(ls_V) < 1 THEN RETURN "C:~\usage_tracking_pending.tmp"

RETURN ls_V[1] + "~\usage_tracking_pending.tmp"

end function

protected function string of_clean (string as_src);string	ls_Old[] = {"~t", "~r", "~n"}
long		ll_I, ll_P

IF IsNull(as_Src) THEN RETURN ""

FOR ll_I = 1 TO 3
	ll_P = Pos(as_Src, ls_Old[ll_I])
	DO WHILE ll_P > 0
		as_Src = Replace(as_Src, ll_P, 1, " ")
		ll_P = Pos(as_Src, ls_Old[ll_I], ll_P + 1)
	LOOP
NEXT

RETURN as_Src

end function

protected function long of_split (string as_line, ref string as_f[]);string	ls_Empty[], ls_Tmp
long		ll_K = 0

as_F = ls_Empty
ls_Tmp = as_Line + "~t"
DO WHILE Pos(ls_Tmp, "~t") > 0
	ll_K ++
	as_F[ll_K] = Left(ls_Tmp, Pos(ls_Tmp, "~t") - 1)
	ls_Tmp = Mid(ls_Tmp, Pos(ls_Tmp, "~t") + 1)
LOOP

RETURN ll_K

end function

protected function datetime of_str_to_dt (string as_val);datetime	ldt_Null

SetNull(ldt_Null)
IF Len(as_Val) < 19 THEN RETURN ldt_Null

RETURN DateTime(Date(Left(as_Val, 10)), Time(Mid(as_Val, 12)))

end function

protected function integer of_save_temp (string as_reason, datetime adt_end, long al_dursec);// Notfall: DB nicht erreichbar → Rohereignisse in Temp-Datei (angehängt, ein Block pro Session)
integer	li_Fn
long		ll_I
string	ls_Line, ls_Ovf = "0"
string	ls_Fmt = "yyyy-mm-dd hh:mm:ss.ffffff"

IF ib_Recovering THEN RETURN -1

li_Fn = FileOpen(of_get_tempfile(), LineMode!, Write!, LockWrite!, Append!, EncodingUTF8!)
IF li_Fn < 0 THEN RETURN -1

IF ib_Overflow THEN ls_Ovf = "1"

// S | session_id | end | dur | reason | ovf | count | user | appver | machine | start
ls_Line = "S~t" + of_clean(is_SessionId) + "~t" + String(adt_End, ls_Fmt) + "~t" + String(al_DurSec) + "~t" + &
          of_clean(as_Reason) + "~t" + ls_Ovf + "~t" + String(il_EvCount) + "~t" + of_clean(is_UserId) + "~t" + &
          of_clean(is_AppVersion) + "~t" + of_clean(is_MachineName) + "~t" + String(idt_SessionStart, ls_Fmt)
IF FileWrite(li_Fn, ls_Line) < 0 THEN
	FileClose(li_Fn)
	RETURN -1
END IF

// E | window | control | type | descr | group | stamp | elapsed_ms
FOR ll_I = 1 TO il_EvCount
	ls_Line = "E~t" + of_clean(istr_Ev[ll_I].window_name) + "~t" + of_clean(istr_Ev[ll_I].control_name) + "~t" + &
	          of_clean(istr_Ev[ll_I].control_type) + "~t" + of_clean(istr_Ev[ll_I].descr) + "~t" + &
	          of_clean(istr_Ev[ll_I].func_group) + "~t" + String(istr_Ev[ll_I].stamp, ls_Fmt) + "~t" + &
	          String(istr_Ev[ll_I].elapsed_ms)
	IF FileWrite(li_Fn, ls_Line) < 0 THEN
		FileClose(li_Fn)
		RETURN -1
	END IF
NEXT

FileClose(li_Fn)

RETURN -2          // -2 = in Temp-Datei ausgelagert

end function

public function long of_recover_temp ();// Beim Start (nach CONNECT, vor of_start_session) aufrufen.
// Liefert Anzahl wiederhergestellter Sessions, -1 bei fehlender DB-Verbindung / Dateifehler.
string		ls_File, ls_Line, ls_All[], ls_F[], ls_Keep[]
string		ls_Sid, ls_Reason, ls_User, ls_Ver, ls_Mach, ls_SaveSid, ls_SaveUser, ls_SaveVer, ls_SaveMach
long			ll_N, ll_I, ll_J, ll_Cnt, ll_Dur, ll_Ok = 0, ll_Rc, ll_Exists, ll_SaveCnt, ll_SaveCpu
integer		li_Fn
boolean		lb_Ovf, lb_SaveOvf, lb_SaveEn
datetime	ldt_End, ldt_Start, ldt_SaveStart
s_usage_event	lstr_Save[], lstr_Empty[]

ls_File = of_get_tempfile()
IF NOT FileExists(ls_File) THEN RETURN 0
IF SQLCA.DBHandle() <= 0 THEN RETURN -1

// ---- 1. Datei lesen ----
li_Fn = FileOpen(ls_File, LineMode!, Read!, LockRead!, Replace!, EncodingUTF8!)
IF li_Fn < 0 THEN RETURN -1
DO
	ll_N = FileRead(li_Fn, ls_Line)
	IF ll_N = -100 THEN EXIT
	IF ll_N < 0 THEN
		FileClose(li_Fn)
		RETURN -1
	END IF
	ls_All[UpperBound(ls_All) + 1] = ls_Line
LOOP WHILE TRUE
FileClose(li_Fn)

// ---- 2. aktuellen Zustand sichern ----
lstr_Save     = istr_Ev
ll_SaveCnt    = il_EvCount
ll_SaveCpu    = il_StartCpu
ls_SaveSid    = is_SessionId
ls_SaveUser   = is_UserId
ls_SaveVer    = is_AppVersion
ls_SaveMach   = is_MachineName
ldt_SaveStart = idt_SessionStart
lb_SaveOvf    = ib_Overflow
lb_SaveEn     = ib_Enabled
ib_Recovering = TRUE

// ---- 3. Blöcke verarbeiten ----
ll_I = 1
DO WHILE ll_I <= UpperBound(ls_All)
	IF Left(ls_All[ll_I], 2) <> "S~t" THEN
		ll_I ++
		CONTINUE
	END IF

	IF of_split(ls_All[ll_I], ls_F) < 11 THEN
		ll_I ++
		CONTINUE
	END IF

	ls_Sid    = ls_F[2]
	ldt_End   = of_str_to_dt(ls_F[3])
	ll_Dur    = Long(ls_F[4])
	ls_Reason = ls_F[5]
	lb_Ovf    = (ls_F[6] = "1")
	ll_Cnt    = Long(ls_F[7])
	ls_User   = ls_F[8]
	ls_Ver    = ls_F[9]
	ls_Mach   = ls_F[10]
	ldt_Start = of_str_to_dt(ls_F[11])

	IF ll_I + ll_Cnt > UpperBound(ls_All) THEN EXIT      // unvollständiger Block (Abbruch beim Schreiben)

	istr_Ev = lstr_Empty
	FOR ll_J = 1 TO ll_Cnt
		of_split(ls_All[ll_I + ll_J], ls_F)
		istr_Ev[ll_J].window_name  = ls_F[2]
		istr_Ev[ll_J].control_name = ls_F[3]
		istr_Ev[ll_J].control_type = ls_F[4]
		istr_Ev[ll_J].descr        = ls_F[5]
		istr_Ev[ll_J].func_group   = ls_F[6]
		istr_Ev[ll_J].stamp        = of_str_to_dt(ls_F[7])
		istr_Ev[ll_J].elapsed_ms   = LongLong(ls_F[8])
	NEXT

	is_SessionId     = ls_Sid
	is_UserId        = ls_User
	is_AppVersion    = ls_Ver
	is_MachineName   = ls_Mach
	idt_SessionStart = ldt_Start
	il_EvCount       = ll_Cnt
	il_StartCpu      = Cpu() - ll_Dur * 1000
	ib_Overflow      = lb_Ovf
	ib_Enabled       = TRUE

	// Kopfzeile fehlt, wenn die DB schon beim Start nicht erreichbar war
	ll_Exists = 0
	SELECT COUNT(*) INTO :ll_Exists FROM org.tracking_session WHERE session_id = :ls_Sid USING SQLCA;
	IF SQLCA.SQLCode = 0 AND ll_Exists = 0 THEN
		INSERT INTO org.tracking_session (session_id, user_id, machine_name, app_version, session_start, close_reason)
		VALUES (:ls_Sid, :ls_User, :ls_Mach, :ls_Ver, :ldt_Start, 'CRASH') USING SQLCA;
		IF SQLCA.SQLCode = 0 THEN COMMIT USING SQLCA;
	END IF

	ll_Rc = of_end_session(ls_Reason)

	IF ll_Rc >= 0 THEN
		// of_end_session schrieb "jetzt" als Ende – ursprüngliche Endzeit zurücksetzen
		UPDATE org.tracking_session SET session_end = :ldt_End WHERE session_id = :ls_Sid USING SQLCA;
		IF SQLCA.SQLCode < 0 THEN
			ROLLBACK USING SQLCA;
		ELSE
			COMMIT USING SQLCA;
		END IF
		ll_Ok ++
	ELSE
		FOR ll_J = 0 TO ll_Cnt
			ls_Keep[UpperBound(ls_Keep) + 1] = ls_All[ll_I + ll_J]
		NEXT
	END IF

	ll_I = ll_I + ll_Cnt + 1
LOOP

// ---- 4. Zustand wiederherstellen ----
istr_Ev          = lstr_Save
il_EvCount       = ll_SaveCnt
il_StartCpu      = ll_SaveCpu
is_SessionId     = ls_SaveSid
is_UserId        = ls_SaveUser
is_AppVersion    = ls_SaveVer
is_MachineName   = ls_SaveMach
idt_SessionStart = ldt_SaveStart
ib_Overflow      = lb_SaveOvf
ib_Enabled       = lb_SaveEn
ib_Recovering    = FALSE

// ---- 5. Datei löschen bzw. nur unverarbeitete Blöcke behalten ----
IF UpperBound(ls_Keep) = 0 THEN
	FileDelete(ls_File)
ELSE
	li_Fn = FileOpen(ls_File, LineMode!, Write!, LockWrite!, Replace!, EncodingUTF8!)
	IF li_Fn >= 0 THEN
		FOR ll_I = 1 TO UpperBound(ls_Keep)
			FileWrite(li_Fn, ls_Keep[ll_I])
		NEXT
		FileClose(li_Fn)
	END IF
END IF

RETURN ll_Ok

end function

on n_cst_usage_tracker.create
call super::create
TriggerEvent( this, "constructor" )
end on

on n_cst_usage_tracker.destroy
TriggerEvent( this, "destructor" )
call super::destroy
end on

event destructor;IF IsValid(inv_Registry) THEN 
	DESTROY inv_Registry
END IF

IF IsValid(inv_Platform) THEN 
	DESTROY inv_Platform
END IF

end event

