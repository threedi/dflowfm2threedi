-- Find out which controlled parameters exist
-- Note that only ca = 3 (pump capacity) is used in this script, all other types are ignored
SELECT ca, count(*) from sobek_control group by ca

-- Make a list of pump capacities before and after update
SELECT pump.code, pump.capacity as old_capacity, sobek_control.ua * 1000 as new_capacity 
from pump,
	 sobek_control
WHERE pump.code = sobek_control.id and sobek_control.ca = 3
;

-- Add a tag for this operation if it does not exist
INSERT INTO tags (description)
SELECT 'Pump capacity is parameter ua * 1000 uit CONTROL.DEF'
WHERE NOT EXISTS (
    SELECT 1 FROM tags WHERE description = 'Pump capacity is parameter ua * 1000 uit CONTROL.DEF'
);

-- List tags
SELECT * FROM tags;

-- Update pump capacities from the sobek_control table (only if controlled paramater is pump capacity (ca=3))
UPDATE pump
SET 	
	capacity = sobek_control.ua * 1000,
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
		WHERE description = 'Pump capacity is parameter ua * 1000 uit CONTROL.DEF'
	) AS tag
WHERE pump.code = sobek_control.id
	AND sobek_control.ca = 3
;



