-- Q-01 / D03: two distinct status sets and a priority-ordered row choice.
-- Overlapping sets calculate independent is_done/is_phoned flags (5/3 codes).
-- They must not be replaced by the same set. Row selection separately depends
-- on ORDER BY priority DESC, created_at, not the earliest timestamp alone.
CREATE OR REPLACE FUNCTION q_out.pick_next_task(p_uid bigint)
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE
    v_task bigint;
    v_is_done boolean;
    v_is_phoned boolean;
BEGIN
    SELECT t.task_id,
           t.status IN ('new', 'ready', 'assigned', 'in_progress', 'waiting') AS is_done,
           t.status IN ('ready', 'in_progress', 'waiting') AS is_phoned
    INTO v_task, v_is_done, v_is_phoned
    FROM q_src.task_queue AS t
    WHERE t.user_id = p_uid
      AND t.is_done IS NOT TRUE
    ORDER BY t.priority DESC, t.created_at
    LIMIT 1;

    UPDATE q_src.task_queue AS u
    SET status = 'assigned', is_done = v_is_done, is_phoned = v_is_phoned
    WHERE u.task_id = v_task;

    RETURN v_task;
END;
$$;
