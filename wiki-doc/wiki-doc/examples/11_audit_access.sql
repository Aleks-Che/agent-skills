CREATE SCHEMA IF NOT EXISTS s_gp_p1024_dmr_svd_kb_ckr_demo_gp_core;
CREATE SCHEMA IF NOT EXISTS s_gp_p1024_dmr_svd_kb_ckr_audit_gp_core;
CREATE TABLE s_gp_p1024_dmr_svd_kb_ckr_audit_gp_core.logs (message text);
CREATE FUNCTION s_gp_p1024_dmr_svd_kb_ckr_audit_gp_core.add_log(p_message text)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO s_gp_p1024_dmr_svd_kb_ckr_audit_gp_core.logs VALUES (p_message);
END;
$$;
CREATE FUNCTION s_gp_p1024_dmr_svd_kb_ckr_demo_gp_core.audit_probe()
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE
    n bigint;
BEGIN
    PERFORM s_gp_p1024_dmr_svd_kb_ckr_audit_gp_core.add_log('start');
    SELECT count(*) INTO n FROM s_gp_p1024_dmr_svd_kb_ckr_audit_gp_core.logs;
    RETURN n;
END;
$$;

