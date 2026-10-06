/*
  Atrod datubāzē laukus, kuros glabāti datumi.  SAP SQL Anywhere 17 (Watcom-SQL).
    NATIVE      - date / timestamp (datetime, smalldatetime) / timestamp with time zone
    STRING_DATE - teksts ar datumu: yyyy-mm-dd, yyyy.mm.dd, dd.mm.yyyy, yyyymmdd (arī ar laiku aiz datuma)
    STRING_YM   - teksts ar mēnesi/gadu: yyyy.mm vai yyyymm (arī yyyy-mm, yyyy/mm)
    INT_YM      - skaitlis yyyymm (piem. 202410)
  Teksta/skaitļu laukus pārbauda pēc DATIEM (paraugs v_sample ierakstu), nevis pēc nosaukuma.
  NULL un '' vērtības tiek ignorētas; visām pārējām vērtībām jāatbilst formātam (100%).
  Skripts tikai lasa datus; izmaiņas netiek veiktas.
*/
BEGIN
    DECLARE v_sample  INT           = 1000;   -- cik ierakstus pārbaudīt katrā laukā
    DECLARE v_minpct  DECIMAL(5,2)  = 100.0;  -- % no parauga, kam jāatbilst formātam (100 = visām ne-tukšām vērtībām)
    DECLARE v_owner   VARCHAR(128)  = '%';    -- ierobežot pēc īpašnieka, piem. 'DBA'

    DECLARE LOCAL TEMPORARY TABLE res (
        owner_name VARCHAR(128), table_name VARCHAR(128), column_name VARCHAR(128),
        data_type  VARCHAR(128), kind VARCHAR(20), hit_pct DECIMAL(5,2) NULL, example VARCHAR(100) NULL
    ) ON COMMIT PRESERVE ROWS;

    /* 1) Natīvie datuma tipi */
    INSERT INTO res (owner_name, table_name, column_name, data_type, kind)
    SELECT u.user_name, t.table_name, c.column_name, d.domain_name, 'NATIVE'
    FROM SYS.SYSTABCOL c
         JOIN SYS.SYSTAB    t ON t.table_id  = c.table_id
         JOIN SYS.SYSUSER   u ON u.user_id   = t.creator
         JOIN SYS.SYSDOMAIN d ON d.domain_id = c.domain_id
    WHERE t.table_type_str = 'BASE' AND t.server_type = 'SA'
      AND u.user_name NOT IN ('SYS','dbo','rs_systabgroup')
      AND u.user_name LIKE v_owner
      AND d.domain_name IN ('date','timestamp','timestamp with time zone');

    /* 2) Teksta un skaitļu lauki - pārbaude pēc satura */
    FOR lp AS cur CURSOR FOR
        SELECT u.user_name AS o, t.table_name AS tb, c.column_name AS cl, d.domain_name AS dt
        FROM SYS.SYSTABCOL c
             JOIN SYS.SYSTAB    t ON t.table_id  = c.table_id
             JOIN SYS.SYSUSER   u ON u.user_id   = t.creator
             JOIN SYS.SYSDOMAIN d ON d.domain_id = c.domain_id
        WHERE t.table_type_str = 'BASE' AND t.server_type = 'SA'
          AND u.user_name NOT IN ('SYS','dbo','rs_systabgroup')
          AND u.user_name LIKE v_owner
          AND ( (d.domain_name IN ('char','varchar','nchar','nvarchar') AND c.width >= 6)
             OR  d.domain_name IN ('integer','bigint','numeric','decimal') )
    DO
        BEGIN
            DECLARE v_sql VARCHAR(8000);
            DECLARE v_tbl VARCHAR(300);
            DECLARE v_col VARCHAR(300);
            SET v_tbl = '"' || o  || '"."' || tb || '"';
            SET v_col = '"' || cl || '"';

            IF dt IN ('integer','bigint','numeric','decimal') THEN
                SET v_sql =
                  'INSERT INTO res SELECT `{O}`,`{T}`,`{C}`,`{D}`,`INT_YM`, pct, ex FROM ('
               || ' SELECT 100.0 * SUM(CASE WHEN v BETWEEN 190001 AND 219912 AND MOD(v,100) BETWEEN 1 AND 12'
               || '                         THEN 1 ELSE 0 END) / COUNT(*) AS pct, CAST(MAX(v) AS VARCHAR(100)) AS ex'
               || ' FROM (SELECT TOP {N} CAST({COL} AS BIGINT) AS v FROM {TBL} WHERE {COL} IS NOT NULL) x'
               || ') y WHERE pct >= {PCT}';
            ELSE
                SET v_sql =
                  'INSERT INTO res SELECT `{O}`,`{T}`,`{C}`,`{D}`, k, pct, ex FROM ('
               || ' SELECT k, 100.0 * cnt / SUM(cnt) OVER () AS pct, ex FROM ('
               || '  SELECT k, COUNT(*) AS cnt, MAX(v) AS ex FROM ('
               || '   SELECT v, CASE'
               || '    WHEN v LIKE `[12][0-9][0-9][0-9].[01][0-9]` AND SUBSTR(v,6,2) BETWEEN `01` AND `12` THEN `STRING_YM`'
               || '    WHEN v LIKE `[12][0-9][0-9][0-9][01][0-9]`  AND SUBSTR(v,5,2) BETWEEN `01` AND `12` THEN `STRING_YM`'
               || '    WHEN v LIKE `[12][0-9][0-9][0-9].[01][0-9].[0-3][0-9]%`'
               || '         AND SUBSTR(v,6,2) BETWEEN `01` AND `12` AND SUBSTR(v,9,2) BETWEEN `01` AND `31` THEN `STRING_DATE`'
               || '    WHEN v LIKE `[0-3][0-9].[01][0-9].[12][0-9][0-9][0-9]%`'
               || '         AND SUBSTR(v,4,2) BETWEEN `01` AND `12` AND SUBSTR(v,1,2) BETWEEN `01` AND `31` THEN `STRING_DATE`'
               || '    WHEN v LIKE `[12][0-9][0-9][0-9][01][0-9][0-3][0-9]`'
               || '         AND SUBSTR(v,5,2) BETWEEN `01` AND `12` AND SUBSTR(v,7,2) BETWEEN `01` AND `31` THEN `STRING_DATE`'
               || '   END AS k'
               || '   FROM (SELECT TOP {N} REPLACE(REPLACE(TRIM(CAST({COL} AS VARCHAR(100))),`-`,`.`),`/`,`.`) AS v'
               || '         FROM {TBL} WHERE {COL} IS NOT NULL AND TRIM(CAST({COL} AS VARCHAR(100))) <> ``) x'
               || '  ) f GROUP BY k'
               || ' ) g'
               || ') h WHERE k IS NOT NULL AND pct >= {PCT}';
            END IF;

            SET v_sql = REPLACE(v_sql, '`',     '''');
            SET v_sql = REPLACE(v_sql, '{TBL}', v_tbl);
            SET v_sql = REPLACE(v_sql, '{COL}', v_col);
            SET v_sql = REPLACE(v_sql, '{N}',   CAST(v_sample AS VARCHAR(10)));
            SET v_sql = REPLACE(v_sql, '{PCT}', CAST(v_minpct AS VARCHAR(10)));
            SET v_sql = REPLACE(v_sql, '{O}',   REPLACE(o,  '''', ''''''));
            SET v_sql = REPLACE(v_sql, '{T}',   REPLACE(tb, '''', ''''''));
            SET v_sql = REPLACE(v_sql, '{C}',   REPLACE(cl, '''', ''''''));
            SET v_sql = REPLACE(v_sql, '{D}',   dt);

            EXECUTE IMMEDIATE v_sql;
        EXCEPTION
            WHEN OTHERS THEN
                MESSAGE 'Izlaists ' || v_tbl || '.' || v_col || ' : ' || ERRORMSG() TO CLIENT;
        END;
    END FOR;

    SELECT owner_name, table_name, column_name, data_type, kind, hit_pct, example
    FROM res
    ORDER BY kind, owner_name, table_name, column_name;
END;
