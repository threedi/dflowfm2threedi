-- Find out which controlled parameters exist
-- Note that only ca = 2 (opening height) is used in this script, all other types are ignored
SELECT ca, count(*) from sobek_control group by ca

-- Make a list of opening heights
SELECT  sobek.id AS code
        CAST(sobek_control.ua AS double precision) as new_opening_height,
FROM sobek_control
WHERE sobek_control.ca = 2
;
