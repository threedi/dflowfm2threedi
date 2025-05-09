-- Find out which controlled parameters exist
-- Note that only ca = 0 (crest level) is used in this script, all other types are ignored
SELECT ca, count(*) from sobek_control group by ca

-- Make a list of crest levels before and after update
SELECT     weir.code, 
        weir.crest_level as old_crest_level, 
        CAST(sobek_control.ua AS double precision) as new_crest_level,
        weir.tags    
FROM weir
JOIN sobek_control ON weir.code = sobek_control.id
WHERE sobek_control.ca = 0
;

-- Add a tag for this operation if it does not exist
INSERT INTO tags (description)
SELECT 'Crest level is parameter ua uit CONTROL.DEF'
WHERE NOT EXISTS (
    SELECT 1 FROM tags WHERE description = 'Crest level is parameter ua uit CONTROL.DEF'
);


-- Update weir crest levels from the sobek_control table (only if controlled paramater is crest_level (ca=0))
UPDATE weir
SET     
    crest_level = sobek_control.ua,
    tags = 
      CASE 
        WHEN tags IS NULL THEN CAST(tag_id AS TEXT)
        WHEN ',' || tags || ',' LIKE '%,' || tag_id || ',%' THEN tags -- already exists
        ELSE tags || ',' || tag_id
      END
FROM
     sobek_control as sobek_control,
    (
        SELECT id AS tag_id
        FROM tags
        WHERE description = 'Crest level is parameter ua uit CONTROL.DEF'
    ) AS tag
WHERE weir.code = sobek_control.id
    AND sobek_control.ca = 0
;