-- Q-01 / D06: one XML aggregate `filters` shape vs five metadata lookups.
-- The document must report exactly ONE xmlagg-shaped aggregation producing the
-- filter list, not five "filter rows". The five q_meta.field_defs lookups are
-- metadata calls and must not be counted as filters (or vice versa).
CREATE OR REPLACE FUNCTION q_out.xml_filter_blob(p_id bigint)
RETURNS xml LANGUAGE plpgsql AS $$
DECLARE
    v_xml   xml;
    v_title text;
    v_kind  text;
    v_owner text;
    v_state text;
    v_extra text;
BEGIN
    SELECT d.def_value INTO v_title FROM q_meta.field_defs AS d
     WHERE d.object_name = 'q_out.xml_filter_blob' AND d.field_name = 'title';
    SELECT d.def_value INTO v_kind  FROM q_meta.field_defs AS d
     WHERE d.object_name = 'q_out.xml_filter_blob' AND d.field_name = 'kind';
    SELECT d.def_value INTO v_owner FROM q_meta.field_defs AS d
     WHERE d.object_name = 'q_out.xml_filter_blob' AND d.field_name = 'owner';
    SELECT d.def_value INTO v_state FROM q_meta.field_defs AS d
     WHERE d.object_name = 'q_out.xml_filter_blob' AND d.field_name = 'state';
    SELECT d.def_value INTO v_extra FROM q_meta.field_defs AS d
     WHERE d.object_name = 'q_out.xml_filter_blob' AND d.field_name = 'extra';

    SELECT xmlelement(
             name "filters",
             xmlagg(xmlelement(name "filter", f.value) ORDER BY f.ord)
           )
      INTO v_xml
      FROM q_src.row_values AS f
     WHERE f.row_id = p_id;

    RETURN v_xml;
END;
$$;
