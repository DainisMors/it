$PBExportHeader$w_eingabe.srw
forward
global type w_eingabe from window
end type
type em_menge from editmask within w_eingabe
end type
type sle_preis from singlelineedit within w_eingabe
end type
end forward

event ue_preis_pruefen;string ls_text
decimal ld_preis
integer li_pos

ls_text = sle_preis.Text
// li_pos = Pos(ls_text, ".")  -- alter Code, Kommentar wird ignoriert
li_pos = Pos(ls_text, ".")
IF li_pos > 0 THEN
	ld_preis = Dec(ls_text)
END IF
IF NOT IsNumber(ls_text) THEN
	MessageBox("Fehler", "Keine Zahl")
END IF
IF Mid(ls_text, 1, 1) = "." THEN ls_text = "0" + ls_text
ls_sql = "UPDATE artikel SET preis = " + String(ld_preis) + " WHERE id = 1"
end event

event key;IF key = KeyPeriod! OR key = KeyDecimal! THEN
	// Dezimaltaste
END IF
end event

event ue_menge;string ls_m
ls_m = of_replaceall(em_menge.Text, ",", ".")
ls_m = String(ld_menge, "#,##0.00")
end event
