/*
  Atrod datubāzē laukus, kuros glabāti datumi:
    1) NATIVE      - date / datetime / datetime2 / smalldatetime / datetimeoffset
    2) STRING_DATE - teksts ar datumu: yyyy-mm-dd, yyyymmdd, yyyy-mm-dd hh:mi...
    3) STRING_YM   - teksts ar mēnesi/gadu: yyyy.mm vai yyyymm (arī yyyy-mm, yyyy/mm)
    4) INT_YM      - skaitlis yyyymm (piem. 202410)
  Teksta/skaitļu laukus pārbauda pēc DATIEM (paraugs @Sample ierakstu), nevis tikai pēc nosaukuma.
  SQL Server (T-SQL). Skripts tikai lasa datus (SELECT), nekādas izmaiņas netiek veiktas.
*/
SET NOCOUNT ON;

DECLARE @Sample    int          = 1000;   -- cik ierakstus pārbaudīt katrā laukā
DECLARE @MinHitPct decimal(5,2) = 90.0;   -- % no parauga, kam jāatbilst formātam
DECLARE @SchemaLike sysname     = N'%';   -- ierobežot pēc shēmas, piem. N'dbo'

IF OBJECT_ID('tempdb..#res') IS NOT NULL DROP TABLE #res;
CREATE TABLE #res (
    SchemaName sysname, TableName sysname, ColumnName sysname,
    DataType   sysname, Kind varchar(20), HitPct decimal(5,2) NULL, Example nvarchar(100) NULL
);

/* 1) Natīvie datuma tipi */
INSERT #res (SchemaName, TableName, ColumnName, DataType, Kind)
SELECT s.name, t.name, c.name, ty.name, 'NATIVE'
FROM sys.columns c
JOIN sys.tables  t  ON t.object_id = c.object_id
JOIN sys.schemas s  ON s.schema_id = t.schema_id
JOIN sys.types   ty ON ty.user_type_id = c.user_type_id
WHERE ty.name IN ('date','datetime','datetime2','smalldatetime','datetimeoffset')
  AND s.name LIKE @SchemaLike;

/* 2) Teksta un veselo skaitļu lauki - pārbaude pēc satura */
DECLARE @sch sysname, @tbl sysname, @col sysname, @typ sysname, @len int, @sql nvarchar(max);

DECLARE cur CURSOR LOCAL FAST_FORWARD FOR
SELECT s.name, t.name, c.name, ty.name, c.max_length
FROM sys.columns c
JOIN sys.tables  t  ON t.object_id = c.object_id
JOIN sys.schemas s  ON s.schema_id = t.schema_id
JOIN sys.types   ty ON ty.user_type_id = c.user_type_id
WHERE s.name LIKE @SchemaLike
  AND (
        (ty.name IN ('char','varchar') AND (c.max_length >= 6 OR c.max_length = -1))
     OR (ty.name IN ('nchar','nvarchar') AND (c.max_length >= 12 OR c.max_length = -1))
     OR ty.name IN ('int','bigint','decimal','numeric')
      );

OPEN cur;
FETCH NEXT FROM cur INTO @sch, @tbl, @col, @typ, @len;
WHILE @@FETCH_STATUS = 0
BEGIN
    DECLARE @q nvarchar(300) = QUOTENAME(@sch) + N'.' + QUOTENAME(@tbl);
    DECLARE @c nvarchar(300) = QUOTENAME(@col);

    IF @typ IN ('int','bigint','decimal','numeric')
        -- yyyymm kā skaitlis (1900-2199, mēnesis 01-12)
        SET @sql = N'
        ;WITH x AS (SELECT TOP (@n) CAST(' + @c + N' AS bigint) v FROM ' + @q + N' WHERE ' + @c + N' IS NOT NULL)
        INSERT #res
        SELECT @s, @t, @cn, @ty, ''INT_YM'',
               100.0 * SUM(CASE WHEN v BETWEEN 190001 AND 219912 AND v % 100 BETWEEN 1 AND 12 THEN 1 ELSE 0 END) / COUNT(*),
               CAST(MAX(v) AS nvarchar(100))
        FROM x HAVING COUNT(*) > 0;';
    ELSE
        SET @sql = N'
        ;WITH x AS (SELECT TOP (@n) LTRIM(RTRIM(CAST(' + @c + N' AS nvarchar(100)))) v
                    FROM ' + @q + N' WHERE ' + @c + N' IS NOT NULL AND ' + @c + N' <> '''')
        , f AS (
            SELECT v,
              CASE
                WHEN v LIKE ''[12][0-9][0-9][0-9][.-/][01][0-9]'' AND SUBSTRING(v,6,2) BETWEEN ''01'' AND ''12'' THEN ''STRING_YM''
                WHEN v LIKE ''[12][0-9][0-9][0-9][01][0-9]''      AND SUBSTRING(v,5,2) BETWEEN ''01'' AND ''12'' THEN ''STRING_YM''
                WHEN v LIKE ''[12][0-9][0-9][0-9][-./][01][0-9][-./][0-3][0-9]%''
                     AND SUBSTRING(v,6,2) BETWEEN ''01'' AND ''12'' AND SUBSTRING(v,9,2) BETWEEN ''01'' AND ''31'' THEN ''STRING_DATE''
                WHEN v LIKE ''[0-3][0-9][-./][01][0-9][-./][12][0-9][0-9][0-9]%''
                     AND SUBSTRING(v,4,2) BETWEEN ''01'' AND ''12'' AND LEFT(v,2) BETWEEN ''01'' AND ''31'' THEN ''STRING_DATE''
                WHEN v LIKE ''[12][0-9][0-9][0-9][01][0-9][0-3][0-9]'' 
                     AND SUBSTRING(v,5,2) BETWEEN ''01'' AND ''12'' AND SUBSTRING(v,7,2) BETWEEN ''01'' AND ''31'' THEN ''STRING_DATE''
              END k
            FROM x)
        INSERT #res
        SELECT TOP (1) @s, @t, @cn, @ty, k,
               100.0 * COUNT(*) / (SELECT COUNT(*) FROM x), MAX(v)
        FROM f WHERE k IS NOT NULL
        GROUP BY k
        HAVING 100.0 * COUNT(*) / (SELECT COUNT(*) FROM x) >= @pct
        ORDER BY COUNT(*) DESC;';

    BEGIN TRY
        EXEC sp_executesql @sql,
             N'@n int, @pct decimal(5,2), @s sysname, @t sysname, @cn sysname, @ty sysname',
             @n = @Sample, @pct = @MinHitPct, @s = @sch, @t = @tbl, @cn = @col, @ty = @typ;
    END TRY
    BEGIN CATCH
        PRINT 'Izlaists ' + @q + '.' + @c + ' : ' + ERROR_MESSAGE();   -- piem. view, bojāti dati, tiesības
    END CATCH;

    FETCH NEXT FROM cur INTO @sch, @tbl, @col, @typ, @len;
END
CLOSE cur; DEALLOCATE cur;

/* INT_YM atstājam tikai, ja ≥ @MinHitPct vērtību atbilst */
DELETE FROM #res WHERE Kind = 'INT_YM' AND HitPct < @MinHitPct;

SELECT SchemaName, TableName, ColumnName, DataType, Kind, HitPct, Example
FROM #res
ORDER BY Kind, SchemaName, TableName, ColumnName;
