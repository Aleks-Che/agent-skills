CREATE TABLE demo.orders (amount numeric);
CREATE FUNCTION core.calc() RETURNS numeric LANGUAGE sql AS $$
SELECT sum(amount * 1.1)
FROM demo.orders
WHERE amount > 0;
$$;
