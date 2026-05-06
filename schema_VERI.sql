--
-- PostgreSQL database dump
--

-- Dumped from database version 9.5.6
-- Dumped by pg_dump version 9.5.24

SET statement_timeout = 0;
SET lock_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: diagnosis; Type: SCHEMA; Schema: -; Owner: tera
--

CREATE SCHEMA diagnosis;


ALTER SCHEMA diagnosis OWNER TO tera;

--
-- Name: plpgsql; Type: EXTENSION; Schema: -; Owner: 
--

CREATE EXTENSION IF NOT EXISTS plpgsql WITH SCHEMA pg_catalog;


--
-- Name: EXTENSION plpgsql; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION plpgsql IS 'PL/pgSQL procedural language';


--
-- Name: arcpar; Type: TYPE; Schema: public; Owner: tera
--

CREATE TYPE public.arcpar AS (
	arcmin integer,
	arcmax integer,
	arcdir integer
);


ALTER TYPE public.arcpar OWNER TO tera;

--
-- Name: dose_calc(integer); Type: FUNCTION; Schema: public; Owner: tera
--

CREATE FUNCTION public.dose_calc(_series_id integer, OUT _dose_real real, OUT _fraction_max integer) RETURNS record
    LANGUAGE plpgsql
    AS $$
DECLARE 
BEGIN
    SELECT sum(dose_real),max(fraction_max) INTO _dose_real,_fraction_max 

	FROM (	SELECT 
		-- wtrtimeplan.field_id, 
		--wtrtimeplan.series_id, wtrtimeplan.totaldose, wtrtimeplan.fractionsnumber, wtrtimeplan.cumulative_weight, wtrtimeplan.plan_id, 
		--wtrtimeplan.par_id, wtrtimeplan.value_plan, wtrtimeplan.insert_tms, wtrtimeplan.totalfielddose, wtrtimeplan.fielddose, wtrtimeplan.trtime_total, 
		--wtrtimereal.lasttreated, wtrtimereal.trtime_sum, wtrtimereal.fraction_count2, wtrtimereal.fraction_count, 
		wtrtimereal.fraction_max,
		wtrtimereal.trtime_sum::double precision / wtrtimeplan.trtime_total::double precision * wtrtimeplan.totalfielddose AS dose_real		
		FROM (
		    SELECT DISTINCT ON (b.field_id) a.series_id, b.field_id,
			a.totaldose * b.cumulative_weight / 100::double precision AS totalfielddose, 
			--a.totaldose, a.fractionsnumber, b.cumulative_weight, c.plan_id, c.par_id, c.value_plan, c.insert_tms,
			CASE WHEN a.fractionsnumber = 0 THEN 0::double precision ELSE a.totaldose * b.cumulative_weight / a.fractionsnumber::double precision / 100::double precision
			END AS fielddose, c.value_plan * a.fractionsnumber AS trtime_total
		    FROM tseries a JOIN tfield b ON (a.series_id = b.series_id AND b.series_id=_series_id) JOIN tplan c USING (field_id)
		    WHERE c.par_id::text = 'SH'::text
		    ORDER BY b.field_id, c.insert_tms DESC) AS wtrtimeplan
		JOIN (	
		    SELECT a.field_id, max(a.fraction_order) AS fraction_max,sum(b.value_real) AS trtime_sum
			-- count(*) AS fraction_count2, 
			-- sum(CASE WHEN c.fraction_status_id = 1 THEN 1 ELSE 0 END) AS fraction_count,
			-- max(c.insert_tms) AS lasttreated, 
		    FROM (SELECT tfraction.* FROM tfraction JOIN tfield ON (tfraction.field_id = tfield.field_id AND series_id = _series_id))  AS a
		    JOIN tfraction_part c USING (fraction_id)
		    JOIN tfraction_data b ON b.fraction_part_id = c.fraction_part_id AND b.par_id::text = 'SH'::text
		    WHERE a.fraction_type_id = 1
		    GROUP BY a.field_id) AS wtrtimereal 
		USING (field_id)
	) AS wtreatment;
END;
$$;


ALTER FUNCTION public.dose_calc(_series_id integer, OUT _dose_real real, OUT _fraction_max integer) OWNER TO tera;

--
-- Name: get_arcpar_tbl(integer, integer, integer); Type: FUNCTION; Schema: public; Owner: tera
--

CREATE FUNCTION public.get_arcpar_tbl(_fraction_type_id integer, _field_id integer, _fraction_order integer) RETURNS SETOF public.arcpar
    LANGUAGE plpgsql
    AS $$

/*
    Return ARCMIN ARCMAX ARCDIR table for given field_id fraction_type_id and fraction_order
*/

DECLARE 
    r ARCPAR%ROWTYPE;
    i int;
        
BEGIN
    FOR i IN SELECT fraction_part_id FROM tfraction 
    JOIN tfraction_part USING (fraction_id)
    WHERE fraction_type_id = _fraction_type_id AND
	  field_id = _field_id AND fraction_order = _fraction_order

    LOOP
	SELECT value_real INTO r.arcmin FROM tfraction_data  WHERE 
	    fraction_part_id = i AND par_id = 'ARCMIN';
	
	SELECT value_real INTO r.arcmax FROM tfraction_data  WHERE 
	    fraction_part_id = i AND par_id = 'ARCMAX';

	SELECT value_real INTO r.arcdir FROM tfraction_data  WHERE 
	    fraction_part_id = i AND par_id = 'ARCDIR';

	RETURN NEXT r;

    END LOOP;
    RETURN;
END;
    
$$;


ALTER FUNCTION public.get_arcpar_tbl(_fraction_type_id integer, _field_id integer, _fraction_order integer) OWNER TO tera;

--
-- Name: get_intervals(date, text, integer); Type: FUNCTION; Schema: public; Owner: tera
--

CREATE FUNCTION public.get_intervals(_visitdate date, _eq_id text, _visitlength integer) RETURNS SETOF interval
    LANGUAGE plpgsql IMMUTABLE
    AS $$
    DECLARE
	_a integer[];
	_intvl integer;
	_row RECORD;
	_j integer = 0;
	_maxa integer;
	_startt integer;
	_outintvl interval;
	    
    BEGIN
		SELECT INTO _intvl extract(hour from timeslot) * 60 + extract (minute from timeslot) FROM teq WHERE eq_id = _eq_id;
		SELECT INTO _startt ( extract(hour from startt) * 60 + extract (minute from startt) ) FROM teq WHERE eq_id = _eq_id;
		SELECT INTO _maxa ( extract(hour from endt-startt) * 60 + extract (minute from endt-startt) ) FROM teq WHERE eq_id = _eq_id;
 

-- cnabrani hodnot jiz planovanych ozareni do pole
		FOR _row IN
	    	SELECT (extract(hour FROM (visittime - startt) ) * 60 + extract(minute FROM (visittime - startt) ) ) as startvisit, visitlength 
			FROM tcalendar JOIN teq USING (eq_id) 
			WHERE eq_id = _eq_id AND visitdate=_visitdate ORDER BY visittime
		LOOP
			FOR i IN 1.._row.visitlength*_intvl LOOP
				_a[_row.startvisit + i] := 1;
			END LOOP;
		END LOOP;
		
--		RAISE NOTICE '%', _a;
-- prochazeni polem a hledani volneho intervalu
		FOR i IN 1.._maxa LOOP
--			RAISE NOTICE 'in for';
--			RAISE NOTICE 'i = %, j = %, a[i] = %', i,_j, _a[i];
			IF _a[i] IS NULL THEN
				_j := _j + 1;
				IF _j = _visitlength*_intvl THEN
					_outintvl := (_startt + i -_j) * '1 minute'::interval;
--					RAISE NOTICE '%', _outintvl;
					_j := 0;
					RETURN NEXT _outintvl;
				END IF;
			ELSE
				_j := 0;
			END IF;
		END LOOP;

    END;

	
$$;


ALTER FUNCTION public.get_intervals(_visitdate date, _eq_id text, _visitlength integer) OWNER TO tera;

--
-- Name: get_next_uid(); Type: FUNCTION; Schema: public; Owner: tera
--

CREATE FUNCTION public.get_next_uid() RETURNS character varying
    LANGUAGE plpgsql
    AS $$
/*
    Create unique SOP Instance UID  : <root>.<suffix>
	use for:
	    SOP Instance UID
	    Series Instance UID
	    Study Instance UID

    #	<suffix> is created:
    #	product    - 1 ... means teragis
    #                2 ... means PLANw Franta Mouric
    # 	system id  - device serial number
    # 	counter    - database sequence
    #   timestamp  - not implemented yet
*/


DECLARE
    rec1 	RECORD;
    rec2   	RECORD;
BEGIN
	SELECT nextval('dicom_serial_seq') INTO rec1;
	SELECT instance_creator_uid() INTO rec2;
	RETURN rec2.instance_creator_uid || '.' || rec1.nextval;
END;
$$;


ALTER FUNCTION public.get_next_uid() OWNER TO tera;

--
-- Name: get_number_ok_fractions(integer, integer); Type: FUNCTION; Schema: public; Owner: tera
--

CREATE FUNCTION public.get_number_ok_fractions(_field_id integer, _fraction_type_id integer) RETURNS integer
    LANGUAGE plpgsql
    AS $$  
/*
    return number of OK fractions in the current field for a particular 
    fraction type 

*/
    
DECLARE 
    _fraction_id tfraction.fraction_id%TYPE;
    _fraction_status_id tfraction_part.fraction_status_id%TYPE;
    count int := 0;
    
BEGIN    
    FOR _fraction_id IN SELECT fraction_id FROM tfraction 
    WHERE field_id = _field_id AND fraction_type_id = _fraction_type_id 
    LOOP
	
	SELECT fraction_status_id INTO _fraction_status_id FROM tfraction_part
	    WHERE fraction_id = _fraction_id AND fraction_status_id = 1;

	IF FOUND THEN
	    count := count + 1;
	END IF;
	
    END LOOP;
    
    RETURN count; 

END;


$$;


ALTER FUNCTION public.get_number_ok_fractions(_field_id integer, _fraction_type_id integer) OWNER TO tera;

--
-- Name: insert_default_plan(integer); Type: FUNCTION; Schema: public; Owner: tera
--

CREATE FUNCTION public.insert_default_plan(_field_id integer) RETURNS integer
    LANGUAGE plpgsql
    AS $$

/*
    Insert default values to tplan table
*/

DECLARE 
    tech_par_ver ttech_par_ver%ROWTYPE ;
    field tfield%ROWTYPE;
    i int := 0;    
    id int;
    
BEGIN
    
    SELECT * INTO field FROM tfield WHERE field_id = _field_id; 

    IF NOT FOUND THEN
	RAISE EXCEPTION '_field_id does not exist in the table tfield' ;
    END IF;

    FOR tech_par_ver IN SELECT *  FROM ttech_par_ver  
    WHERE  eq_id = field.eq_id AND tech_id = field.tech_id AND
	  default_value IS NOT NULL

    LOOP

	-- check if data are saved in the tplan already
	SELECT plan_id INTO id FROM tplan
	WHERE field_id = _field_id AND par_id = tech_par_ver.par_id;
	
	IF FOUND THEN 
	    RAISE NOTICE 'par_id skip %',tech_par_ver.par_id;
	    CONTINUE;
	END IF;
	

	INSERT INTO tplan (field_id,par_id,value_plan)
	VALUES (_field_id, tech_par_ver.par_id,tech_par_ver.default_value ) ;
	i := i + 1;
	RAISE NOTICE 'insert default value for %',tech_par_ver.par_id;
	
    END LOOP;

    RETURN i;
END;
    
$$;


ALTER FUNCTION public.insert_default_plan(_field_id integer) OWNER TO tera;

--
-- Name: insert_fraction(integer, integer, integer); Type: FUNCTION; Schema: public; Owner: tera
--

CREATE FUNCTION public.insert_fraction(tp integer, fid integer, number integer) RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE
    id integer := 0;
    myrec RECORD;
    seq RECORD;
BEGIN
    SELECT fraction_id INTO myrec FROM tfraction WHERE fraction_type_id=tp AND field_id=fid AND fraction_order=number;
    IF NOT FOUND THEN
	SELECT nextval('tfraction_id_seq') INTO seq;
	id := seq.nextval;

	INSERT INTO tfraction (fraction_id,fraction_type_id,field_id,fraction_order) VALUES (id,tp,fid,number);
    ELSE
	id := myrec.fraction_id;
    END IF;

    RETURN id;
END;
$$;


ALTER FUNCTION public.insert_fraction(tp integer, fid integer, number integer) OWNER TO tera;

--
-- Name: insert_ttol_value(); Type: FUNCTION; Schema: public; Owner: tera
--

CREATE FUNCTION public.insert_ttol_value() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    rec RECORD;
BEGIN
    
    SELECT * INTO rec FROM ttol_value WHERE tol_id = new.tol_id AND par_id = new.par_id;
    IF FOUND THEN
	UPDATE ttol_value SET value = new.value WHERE (tol_id = new.tol_id AND par_id = new.par_id);
	RETURN NULL;
    END IF;

    RETURN new;
END;
$$;


ALTER FUNCTION public.insert_ttol_value() OWNER TO tera;

--
-- Name: instance_creator_uid(); Type: FUNCTION; Schema: public; Owner: tera
--

CREATE FUNCTION public.instance_creator_uid() RETURNS character varying
    LANGUAGE plpgsql
    AS $$
/*
    return DICOM Instance Creator UID  
	create it from the tconfig table
*/

DECLARE
    rec_root   RECORD;
    rec_serial RECORD;
BEGIN
    SELECT val INTO rec_root FROM tconfig WHERE config_id='ROOT';
    IF NOT FOUND THEN
	RAISE EXCEPTION 'tconfig table is not configured for ROOT config_id' ;
    END IF;
    SELECT val INTO rec_serial FROM tconfig WHERE config_id='SERIAL_ID';
    IF NOT FOUND THEN
	RAISE EXCEPTION 'tconfig table is not configured for SERIAL_ID config_id' ;
    END IF;

    RETURN  rec_root.val || '1.' || rec_serial.val;
END;
$$;


ALTER FUNCTION public.instance_creator_uid() OWNER TO tera;

--
-- Name: notify_series_change(); Type: FUNCTION; Schema: public; Owner: tera
--

CREATE FUNCTION public.notify_series_change() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    notification_data JSON;
BEGIN
    IF NEW.series_status_id = 1 THEN
        notification_data := json_build_object(
            'patient_id', NEW.patient_id,
            'patient_unique_number', (SELECT patient_unique_number FROM tpatient WHERE patient_id = NEW.patient_id),
            'surname', (SELECT surname FROM tpatient WHERE patient_id = NEW.patient_id),
            'forename', (SELECT forename FROM tpatient WHERE patient_id = NEW.patient_id),
            'series_id', NEW.series_id,
            'name', NEW.name,
            'totaldose', NEW.totaldose,
            'fractionsnumber', NEW.fractionsnumber,
            'note', NEW.note
        );
        PERFORM pg_notify('series_changes', notification_data::text);
    END IF;
    RETURN NEW;
END;
$$;


ALTER FUNCTION public.notify_series_change() OWNER TO tera;

--
-- Name: removelargeobject(); Type: FUNCTION; Schema: public; Owner: tera
--

CREATE FUNCTION public.removelargeobject() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    DECLARE
	myrec RECORD;
    BEGIN
	BEGIN
	SELECT lo_unlink(OLD.image) INTO myrec;

	EXCEPTION WHEN undefined_object THEN
	    RAISE NOTICE 'chyba %: %',OLD.image,SQLSTATE;
	END;

	RETURN OLD;
    END;
$$;


ALTER FUNCTION public.removelargeobject() OWNER TO tera;

--
-- Name: to_ascii(bytea, name); Type: FUNCTION; Schema: public; Owner: tera
--

CREATE FUNCTION public.to_ascii(bytea, name) RETURNS text
    LANGUAGE internal STRICT
    AS $$to_ascii_encname$$;


ALTER FUNCTION public.to_ascii(bytea, name) OWNER TO tera;

--
-- Name: update_exportseriesuid(); Type: FUNCTION; Schema: public; Owner: tera
--

CREATE FUNCTION public.update_exportseriesuid() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
	_series_id integer;
	myrec	RECORD;
BEGIN
	IF TG_RELNAME = 'tseries' THEN
	    IF 
	      OLD.export_seriesinstanceuid IS NULL or 
	     (NEW.export_seriesinstanceuid IS NOT NULL AND 
	      NEW.export_seriesinstanceuid != OLD.export_seriesinstanceuid)  
	    THEN
		RETURN NEW;
	    END IF;
	END IF;

	IF TG_RELNAME = 'tseries' OR TG_RELNAME = 'tfield' THEN
		_series_id := NEW.series_id;
	ELSIF TG_RELNAME = 'tsequence_item' THEN
			SELECT object, object_id INTO myrec 
			FROM tsequence
				JOIN tsequence_name USING (sequence_name_id) 
			WHERE sequence_id = NEW.sequence_id;
			IF myrec.object = 'tseries' THEN
				_series_id := myrec.object_id;
			ELSIF myrec.object = 'tplan' THEN
				SELECT series_id INTO _series_id
				FROM tplan
					JOIN tfield USING (field_id)
				WHERE plan_id = myrec.object_id;
	 		ELSE
				RETURN NEW;
			END IF;
	ELSIF TG_RELNAME = 'tplan' THEN 
		SELECT series_id INTO _series_id 
		FROM tfield
		WHERE field_id = NEW.field_id;
	ELSIF TG_RELNAME = 'tplan_item' THEN
		SELECT series_id INTO _series_id
		FROM tplan
			JOIN tfield USING (field_id)
		WHERE plan_id = NEW.plan_id;
	END IF;

	UPDATE tseries SET export_seriesinstanceuid = NULL WHERE series_id = _series_id;
    
	RETURN NEW;
END;
$$;


ALTER FUNCTION public.update_exportseriesuid() OWNER TO tera;

SET default_tablespace = '';

SET default_with_oids = false;

--
-- Name: tdiagnosis; Type: TABLE; Schema: diagnosis; Owner: tera
--

CREATE TABLE diagnosis.tdiagnosis (
    diagnosis_id character varying(5) NOT NULL,
    group_id integer,
    diagnosis_desc1 text,
    diagnosis_desc2 text,
    diagnosis_desc3 text
);


ALTER TABLE diagnosis.tdiagnosis OWNER TO tera;

--
-- Name: tgroup; Type: TABLE; Schema: diagnosis; Owner: tera
--

CREATE TABLE diagnosis.tgroup (
    group_id integer NOT NULL,
    group_name1 text,
    group_name2 text,
    group_name3 text
);


ALTER TABLE diagnosis.tgroup OWNER TO tera;

--
-- Name: tgroup_group_id_seq; Type: SEQUENCE; Schema: diagnosis; Owner: tera
--

CREATE SEQUENCE diagnosis.tgroup_group_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE diagnosis.tgroup_group_id_seq OWNER TO tera;

--
-- Name: tgroup_group_id_seq; Type: SEQUENCE OWNED BY; Schema: diagnosis; Owner: tera
--

ALTER SEQUENCE diagnosis.tgroup_group_id_seq OWNED BY diagnosis.tgroup.group_id;


--
-- Name: dicom_serial_seq; Type: SEQUENCE; Schema: public; Owner: tera
--

CREATE SEQUENCE public.dicom_serial_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.dicom_serial_seq OWNER TO tera;

--
-- Name: sequence_id_seq; Type: SEQUENCE; Schema: public; Owner: tera
--

CREATE SEQUENCE public.sequence_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.sequence_id_seq OWNER TO tera;

--
-- Name: sequence_item_id_seq; Type: SEQUENCE; Schema: public; Owner: tera
--

CREATE SEQUENCE public.sequence_item_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.sequence_item_id_seq OWNER TO tera;

--
-- Name: tcalendar_id_seq; Type: SEQUENCE; Schema: public; Owner: tera
--

CREATE SEQUENCE public.tcalendar_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.tcalendar_id_seq OWNER TO tera;

--
-- Name: tcalendar; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.tcalendar (
    calendar_id integer DEFAULT nextval('public.tcalendar_id_seq'::regclass) NOT NULL,
    series_id integer NOT NULL,
    calendar_status_id smallint NOT NULL,
    eq_id character varying NOT NULL,
    insert_tms timestamp without time zone DEFAULT now(),
    insert_user character varying DEFAULT "current_user"(),
    visitdate date NOT NULL,
    visittime time without time zone NOT NULL,
    visitlength smallint DEFAULT 1,
    fraction_order smallint,
    note text
);


ALTER TABLE public.tcalendar OWNER TO tera;

--
-- Name: tcalendar_status; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.tcalendar_status (
    calendar_status_id smallint NOT NULL,
    name character varying NOT NULL
);


ALTER TABLE public.tcalendar_status OWNER TO tera;

--
-- Name: tconfig; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.tconfig (
    config_id character varying NOT NULL,
    val character varying,
    description character varying
);


ALTER TABLE public.tconfig OWNER TO tera;

--
-- Name: tdepartment; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.tdepartment (
    department_id integer NOT NULL,
    name character varying
);


ALTER TABLE public.tdepartment OWNER TO tera;

--
-- Name: tdiagnosis_series; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.tdiagnosis_series (
    series_id integer NOT NULL,
    diagnosis_id character varying(5) NOT NULL
);


ALTER TABLE public.tdiagnosis_series OWNER TO tera;

--
-- Name: teq; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.teq (
    eq_id character varying NOT NULL,
    eq_type_id character(1) DEFAULT 'I'::bpchar,
    name character varying,
    sad integer,
    startt time without time zone DEFAULT '07:00:00'::time without time zone,
    endt time without time zone DEFAULT '17:00:00'::time without time zone,
    timeslot interval DEFAULT '00:10:00'::interval,
    wdays character varying DEFAULT '1111100'::character varying,
    weekstart integer DEFAULT 0,
    options json
);


ALTER TABLE public.teq OWNER TO tera;

--
-- Name: teq_par; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.teq_par (
    eq_id character varying NOT NULL,
    par_id character varying NOT NULL,
    unit_id character varying,
    minpos integer,
    maxpos integer
);


ALTER TABLE public.teq_par OWNER TO tera;

--
-- Name: teq_type; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.teq_type (
    eq_type_id character(1) NOT NULL,
    name character varying
);


ALTER TABLE public.teq_type OWNER TO tera;

--
-- Name: tfield_id_seq; Type: SEQUENCE; Schema: public; Owner: tera
--

CREATE SEQUENCE public.tfield_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.tfield_id_seq OWNER TO tera;

--
-- Name: tfield; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.tfield (
    field_id integer DEFAULT nextval('public.tfield_id_seq'::regclass) NOT NULL,
    eq_id character varying NOT NULL,
    series_id integer NOT NULL,
    tol_id integer NOT NULL,
    tech_id character varying NOT NULL,
    field_order smallint NOT NULL,
    cumulative_weight real,
    name character varying,
    note text,
    patient_position character varying,
    isocenter_position character varying DEFAULT '0 0 0'::character varying,
    insert_tms timestamp without time zone DEFAULT now(),
    insert_user character varying DEFAULT "current_user"()
);


ALTER TABLE public.tfield OWNER TO tera;

--
-- Name: tfraction_id_seq; Type: SEQUENCE; Schema: public; Owner: tera
--

CREATE SEQUENCE public.tfraction_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.tfraction_id_seq OWNER TO tera;

--
-- Name: tfraction; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.tfraction (
    fraction_id integer DEFAULT nextval('public.tfraction_id_seq'::regclass) NOT NULL,
    fraction_type_id smallint NOT NULL,
    field_id integer NOT NULL,
    fraction_order smallint NOT NULL,
    insert_tms timestamp without time zone DEFAULT now(),
    insert_user character varying DEFAULT "current_user"()
);


ALTER TABLE public.tfraction OWNER TO tera;

--
-- Name: tfraction_data; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.tfraction_data (
    fraction_part_id integer NOT NULL,
    par_id character varying NOT NULL,
    value_real integer,
    verified boolean DEFAULT true
);


ALTER TABLE public.tfraction_data OWNER TO tera;

--
-- Name: tfraction_part_id_seq; Type: SEQUENCE; Schema: public; Owner: tera
--

CREATE SEQUENCE public.tfraction_part_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.tfraction_part_id_seq OWNER TO tera;

--
-- Name: tfraction_part; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.tfraction_part (
    fraction_part_id integer DEFAULT nextval('public.tfraction_part_id_seq'::regclass) NOT NULL,
    fraction_id integer NOT NULL,
    insert_tms timestamp without time zone DEFAULT now(),
    insert_user character varying DEFAULT "current_user"(),
    insert_type character(1) DEFAULT 'A'::bpchar,
    fraction_status_id smallint DEFAULT 0,
    note text
);


ALTER TABLE public.tfraction_part OWNER TO tera;

--
-- Name: tfraction_status; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.tfraction_status (
    fraction_status_id smallint NOT NULL,
    name character varying
);


ALTER TABLE public.tfraction_status OWNER TO tera;

--
-- Name: tfraction_type; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.tfraction_type (
    fraction_type_id smallint NOT NULL,
    name character varying
);


ALTER TABLE public.tfraction_type OWNER TO tera;

--
-- Name: tholiday; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.tholiday (
    hdate date NOT NULL,
    name character varying
);


ALTER TABLE public.tholiday OWNER TO tera;

--
-- Name: timage_id_seq; Type: SEQUENCE; Schema: public; Owner: tera
--

CREATE SEQUENCE public.timage_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.timage_id_seq OWNER TO tera;

--
-- Name: timage; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.timage (
    image_id integer DEFAULT nextval('public.timage_id_seq'::regclass) NOT NULL,
    object character varying,
    id integer,
    image oid,
    name character varying,
    insert_tms timestamp without time zone DEFAULT now(),
    insert_user character varying DEFAULT "current_user"()
);


ALTER TABLE public.timage OWNER TO tera;

--
-- Name: timport_id_seq; Type: SEQUENCE; Schema: public; Owner: tera
--

CREATE SEQUENCE public.timport_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.timport_id_seq OWNER TO tera;

--
-- Name: timport; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.timport (
    import_id integer DEFAULT nextval('public.timport_id_seq'::regclass) NOT NULL,
    importcreator_id integer,
    treatmentmachinename character varying,
    eq_id character varying
);


ALTER TABLE public.timport OWNER TO tera;

--
-- Name: timport_par_map; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.timport_par_map (
    importcreator_id integer NOT NULL,
    eq_id character varying NOT NULL,
    par_id character varying NOT NULL,
    oldval character varying NOT NULL,
    newval character varying NOT NULL
);


ALTER TABLE public.timport_par_map OWNER TO tera;

--
-- Name: timportcreator_id_seq; Type: SEQUENCE; Schema: public; Owner: tera
--

CREATE SEQUENCE public.timportcreator_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.timportcreator_id_seq OWNER TO tera;

--
-- Name: timportcreator; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.timportcreator (
    importcreator_id integer DEFAULT nextval('public.timportcreator_id_seq'::regclass) NOT NULL,
    instancecreatoruid character varying,
    manufacturer character varying,
    manufacturermodelname character varying,
    softwareversion character varying
);


ALTER TABLE public.timportcreator OWNER TO tera;

--
-- Name: tinsurance; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.tinsurance (
    insurance_id integer NOT NULL,
    name character varying NOT NULL,
    id character varying
);


ALTER TABLE public.tinsurance OWNER TO tera;

--
-- Name: tlog_id_seq; Type: SEQUENCE; Schema: public; Owner: tera
--

CREATE SEQUENCE public.tlog_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.tlog_id_seq OWNER TO tera;

--
-- Name: tlog; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.tlog (
    log_id integer DEFAULT nextval('public.tlog_id_seq'::regclass) NOT NULL,
    log_type_id integer,
    log_status_id smallint,
    object character varying,
    id integer,
    insert_tms timestamp without time zone DEFAULT now(),
    inser_user character varying DEFAULT "current_user"(),
    title character varying,
    description text,
    more_description text
);


ALTER TABLE public.tlog OWNER TO tera;

--
-- Name: tlog_status; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.tlog_status (
    log_status_id smallint NOT NULL,
    name character varying NOT NULL
);


ALTER TABLE public.tlog_status OWNER TO tera;

--
-- Name: tlog_type; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.tlog_type (
    log_type_id integer NOT NULL,
    name character varying NOT NULL,
    description character varying
);


ALTER TABLE public.tlog_type OWNER TO tera;

--
-- Name: tpar; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.tpar (
    par_id character varying NOT NULL,
    par_type_id character varying,
    par_gr_id character varying,
    name character varying
);


ALTER TABLE public.tpar OWNER TO tera;

--
-- Name: tpar_gr; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.tpar_gr (
    par_gr_id character varying NOT NULL,
    name character varying
);


ALTER TABLE public.tpar_gr OWNER TO tera;

--
-- Name: tpar_type; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.tpar_type (
    par_type_id character varying NOT NULL,
    name character varying
);


ALTER TABLE public.tpar_type OWNER TO tera;

--
-- Name: tpatient_id_seq; Type: SEQUENCE; Schema: public; Owner: tera
--

CREATE SEQUENCE public.tpatient_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.tpatient_id_seq OWNER TO tera;

--
-- Name: tpatient; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.tpatient (
    patient_id integer DEFAULT nextval('public.tpatient_id_seq'::regclass) NOT NULL,
    patient_unique_number character varying NOT NULL,
    sex character varying DEFAULT 'other'::character varying,
    surname character varying NOT NULL,
    forename character varying,
    title character varying,
    birthdate date,
    insurance_id integer,
    street character varying,
    streetnumber character varying,
    city character varying,
    postcode character varying,
    phone_home character varying,
    phone_work character varying,
    email character varying,
    insert_tms timestamp without time zone DEFAULT now(),
    insert_user character varying DEFAULT "current_user"()
);


ALTER TABLE public.tpatient OWNER TO tera;

--
-- Name: tplan_id_seq; Type: SEQUENCE; Schema: public; Owner: tera
--

CREATE SEQUENCE public.tplan_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.tplan_id_seq OWNER TO tera;

--
-- Name: tplan; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.tplan (
    plan_id integer DEFAULT nextval('public.tplan_id_seq'::regclass) NOT NULL,
    field_id integer,
    par_id character varying NOT NULL,
    value_plan integer NOT NULL,
    insert_tms timestamp without time zone DEFAULT now(),
    insert_user character varying DEFAULT "current_user"()
);


ALTER TABLE public.tplan OWNER TO tera;

--
-- Name: tplan_item_id_seq; Type: SEQUENCE; Schema: public; Owner: tera
--

CREATE SEQUENCE public.tplan_item_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.tplan_item_id_seq OWNER TO tera;

--
-- Name: tplan_item; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.tplan_item (
    plan_item_id integer DEFAULT nextval('public.tplan_item_id_seq'::regclass) NOT NULL,
    plan_id integer NOT NULL,
    name character varying,
    value character varying,
    plan_item_group_id integer
);


ALTER TABLE public.tplan_item OWNER TO tera;

--
-- Name: tsequence; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.tsequence (
    sequence_id integer DEFAULT nextval('public.sequence_id_seq'::regclass) NOT NULL,
    sequence_name_id integer NOT NULL,
    object_id integer
);


ALTER TABLE public.tsequence OWNER TO tera;

--
-- Name: tsequence_item; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.tsequence_item (
    sequence_item_id integer DEFAULT nextval('public.sequence_item_id_seq'::regclass) NOT NULL,
    sequence_id integer NOT NULL,
    row_id integer DEFAULT 0,
    name character varying NOT NULL,
    type character(2),
    value character varying
);


ALTER TABLE public.tsequence_item OWNER TO tera;

--
-- Name: tsequence_name; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.tsequence_name (
    sequence_name_id integer NOT NULL,
    sequence_name character varying NOT NULL,
    object character varying
);


ALTER TABLE public.tsequence_name OWNER TO tera;

--
-- Name: tseries_id_seq; Type: SEQUENCE; Schema: public; Owner: tera
--

CREATE SEQUENCE public.tseries_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.tseries_id_seq OWNER TO tera;

--
-- Name: tseries; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.tseries (
    series_id integer DEFAULT nextval('public.tseries_id_seq'::regclass) NOT NULL,
    patient_id integer NOT NULL,
    series_status_id smallint DEFAULT 2 NOT NULL,
    doctor_id character varying,
    name character varying,
    totaldose real,
    fractionsnumber smallint,
    note text,
    status_tms timestamp without time zone,
    status_user character varying,
    insert_tms timestamp without time zone DEFAULT now(),
    insert_user character varying DEFAULT "current_user"(),
    studyinstanceuid character varying DEFAULT public.get_next_uid(),
    seriesinstanceuid character varying,
    sopinstanceuid character varying DEFAULT public.get_next_uid(),
    numberoffractionpatterndigitsperday smallint,
    repeatfractioncyclelength smallint,
    fractionpattern character varying,
    importcreator_id integer,
    instancecreatoruid character varying DEFAULT public.instance_creator_uid(),
    export_seriesinstanceuid character varying
);


ALTER TABLE public.tseries OWNER TO tera;

--
-- Name: tseries_status; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.tseries_status (
    series_status_id smallint NOT NULL,
    name character varying
);


ALTER TABLE public.tseries_status OWNER TO tera;

--
-- Name: tsex; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.tsex (
    sex character varying NOT NULL,
    name character varying
);


ALTER TABLE public.tsex OWNER TO tera;

--
-- Name: ttech_par_ver; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.ttech_par_ver (
    eq_id character varying NOT NULL,
    par_id character varying NOT NULL,
    tech_id character varying NOT NULL,
    verify boolean,
    acquire boolean,
    autosetup boolean,
    default_value integer
);


ALTER TABLE public.ttech_par_ver OWNER TO tera;

--
-- Name: ttechnique; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.ttechnique (
    tech_id character varying NOT NULL,
    name character varying
);


ALTER TABLE public.ttechnique OWNER TO tera;

--
-- Name: ttol_id_seq; Type: SEQUENCE; Schema: public; Owner: tera
--

CREATE SEQUENCE public.ttol_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.ttol_id_seq OWNER TO tera;

--
-- Name: ttol; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.ttol (
    tol_id integer DEFAULT nextval('public.ttol_id_seq'::regclass) NOT NULL,
    name character varying
);


ALTER TABLE public.ttol OWNER TO tera;

--
-- Name: ttol_value; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.ttol_value (
    tol_id integer NOT NULL,
    par_id character varying NOT NULL,
    value integer
);


ALTER TABLE public.ttol_value OWNER TO tera;

--
-- Name: tunits; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.tunits (
    unit_id character varying NOT NULL,
    par_type_id character varying,
    name character varying,
    ratio real,
    dec_places smallint
);


ALTER TABLE public.tunits OWNER TO tera;

--
-- Name: tuser_id_seq; Type: SEQUENCE; Schema: public; Owner: tera
--

CREATE SEQUENCE public.tuser_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.tuser_id_seq OWNER TO tera;

--
-- Name: tuser; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.tuser (
    user_id integer DEFAULT nextval('public.tuser_id_seq'::regclass) NOT NULL,
    login character varying NOT NULL,
    user_type_id character(1) NOT NULL,
    department_id integer,
    surname character varying NOT NULL,
    forename character varying,
    title character varying,
    phone_work character varying,
    phone_home character varying,
    email character varying
);


ALTER TABLE public.tuser OWNER TO tera;

--
-- Name: tuser_type; Type: TABLE; Schema: public; Owner: tera
--

CREATE TABLE public.tuser_type (
    user_type_id character(1) NOT NULL,
    name character varying NOT NULL
);


ALTER TABLE public.tuser_type OWNER TO tera;

--
-- Name: wcalendar; Type: VIEW; Schema: public; Owner: tera
--

CREATE VIEW public.wcalendar AS
 SELECT a.patient_id,
    a.surname,
    a.forename,
    a.patient_unique_number,
    a.street,
    a.streetnumber,
    a.city,
    b.series_id,
    b.name AS series_name,
    b.series_status_id,
    b.fractionsnumber,
    d.name AS series_status_name,
    c.fraction_order,
    c.calendar_id,
    c.calendar_status_id,
    c.eq_id,
    c.visitdate,
    c.visittime,
    c.visitlength,
    to_char((c.visittime)::interval, 'HH24:MI'::text) AS hourmin,
    e.name AS calendar_status_name,
    c.note,
    c.visittime AS starttime,
    (c.visittime + ((c.visitlength)::double precision * f.timeslot)) AS endtime,
    f.timeslot
   FROM (((((public.tcalendar c
     JOIN public.tcalendar_status e USING (calendar_status_id))
     JOIN public.tseries b USING (series_id))
     JOIN public.tseries_status d USING (series_status_id))
     JOIN public.tpatient a USING (patient_id))
     JOIN public.teq f USING (eq_id));


ALTER TABLE public.wcalendar OWNER TO tera;

--
-- Name: wcalendartime; Type: VIEW; Schema: public; Owner: tera
--

CREATE VIEW public.wcalendartime AS
 SELECT a.calendar_id,
    a.series_id,
    a.calendar_status_id,
    a.eq_id,
    a.insert_tms,
    a.insert_user,
    a.visitdate,
    a.visittime,
    a.visitlength,
    a.fraction_order,
    a.note,
    a.visittime AS starttime,
    (a.visittime + ((a.visitlength)::double precision * b.timeslot)) AS endtime,
    b.timeslot
   FROM (public.tcalendar a
     JOIN public.teq b USING (eq_id));


ALTER TABLE public.wcalendartime OWNER TO tera;

--
-- Name: wdiagnosis_series; Type: VIEW; Schema: public; Owner: tera
--

CREATE VIEW public.wdiagnosis_series AS
 SELECT tdiagnosis_series.series_id,
    array_to_string(array_agg(tdiagnosis_series.diagnosis_id), ', '::text) AS diagnosis_id
   FROM public.tdiagnosis_series
  GROUP BY tdiagnosis_series.series_id;


ALTER TABLE public.wdiagnosis_series OWNER TO tera;

--
-- Name: weq_tech_par; Type: VIEW; Schema: public; Owner: tera
--

CREATE VIEW public.weq_tech_par AS
 SELECT a.eq_id,
    a.par_id,
    a.unit_id,
    b.tech_id,
    b.verify,
    b.acquire,
    c.ratio,
    c.dec_places,
    c.name AS unit_name,
    d.name AS par_name,
    d.par_gr_id,
    d.par_type_id,
    e.name AS par_gr_name
   FROM ((((public.teq_par a
     JOIN public.ttech_par_ver b USING (eq_id, par_id))
     JOIN public.tunits c USING (unit_id))
     JOIN public.tpar d USING (par_id))
     JOIN public.tpar_gr e USING (par_gr_id));


ALTER TABLE public.weq_tech_par OWNER TO tera;

--
-- Name: wfield; Type: VIEW; Schema: public; Owner: tera
--

CREATE VIEW public.wfield AS
 SELECT a.fractionsnumber,
    b.field_id,
    b.eq_id,
    b.series_id,
    b.tol_id,
    b.tech_id,
    b.field_order,
    b.cumulative_weight,
    b.name,
    b.note,
    b.patient_position,
    b.isocenter_position,
    b.insert_tms,
    b.insert_user,
    c.name AS tol_name
   FROM ((public.tseries a
     JOIN public.tfield b USING (series_id))
     JOIN public.ttol c USING (tol_id));


ALTER TABLE public.wfield OWNER TO tera;

--
-- Name: wfield_dose; Type: VIEW; Schema: public; Owner: tera
--

CREATE VIEW public.wfield_dose AS
 SELECT treat_plan.series_id,
    treat_plan.field_id,
    treat_real.sh_total,
    treat_plan.fielddose_persecond,
    ((treat_real.sh_total)::double precision * treat_plan.fielddose_persecond) AS dose,
    treat_real.fraction_count
   FROM (( SELECT se.series_id,
            se.totaldose,
            se.fractionsnumber,
            fi.field_id,
            fi.cumulative_weight,
            sh.sh_plan,
                CASE
                    WHEN (se.fractionsnumber = 0) THEN (0)::double precision
                    ELSE ((se.totaldose * fi.cumulative_weight) / (((100 * se.fractionsnumber) * sh.sh_plan))::double precision)
                END AS fielddose_persecond
           FROM ((public.tseries se
             JOIN public.tfield fi USING (series_id))
             JOIN ( SELECT DISTINCT ON (tplan.field_id) tplan.field_id,
                    tplan.value_plan AS sh_plan
                   FROM public.tplan
                  WHERE ((tplan.par_id)::text = 'SH'::text)
                  ORDER BY tplan.field_id, tplan.insert_tms DESC) sh USING (field_id))) treat_plan
     JOIN ( SELECT fr.field_id,
            sum(
                CASE
                    WHEN (frp.fraction_status_id = 1) THEN 1
                    ELSE 0
                END) AS fraction_count,
            sum(frd.value_real) AS sh_total
           FROM ((public.tfraction fr
             JOIN public.tfraction_part frp USING (fraction_id))
             JOIN public.tfraction_data frd ON (((frp.fraction_part_id = frd.fraction_part_id) AND ((frd.par_id)::text = 'SH'::text))))
          GROUP BY fr.field_id) treat_real USING (field_id))
  ORDER BY treat_plan.series_id, treat_plan.field_id;


ALTER TABLE public.wfield_dose OWNER TO tera;

--
-- Name: wfraction_data_unit; Type: VIEW; Schema: public; Owner: tera
--

CREATE VIEW public.wfraction_data_unit AS
 SELECT a.fraction_part_id,
    b.fraction_id,
    b.insert_tms,
    c.field_id,
    a.par_id,
    round((((a.value_real)::double precision * g.ratio))::numeric, (g.dec_places)::integer) AS value_real,
    f.unit_id
   FROM (((((public.tfraction_data a
     JOIN public.tfraction_part b USING (fraction_part_id))
     JOIN public.tfraction c USING (fraction_id))
     JOIN public.tfield d USING (field_id))
     JOIN public.teq_par f ON ((((d.eq_id)::text = (f.eq_id)::text) AND ((a.par_id)::text = (f.par_id)::text))))
     JOIN public.tunits g USING (unit_id));


ALTER TABLE public.wfraction_data_unit OWNER TO tera;

--
-- Name: wplan_data_unit; Type: VIEW; Schema: public; Owner: tera
--

CREATE VIEW public.wplan_data_unit AS
 SELECT a.plan_id,
    a.field_id,
    a.par_id,
    round((((a.value_plan)::double precision * f.ratio))::numeric, (f.dec_places)::integer) AS value_plan,
    a.insert_tms,
    a.insert_user
   FROM (((public.tplan a
     JOIN public.tfield c USING (field_id))
     JOIN public.teq_par e ON ((((c.eq_id)::text = (e.eq_id)::text) AND ((a.par_id)::text = (e.par_id)::text))))
     JOIN public.tunits f USING (unit_id));


ALTER TABLE public.wplan_data_unit OWNER TO tera;

--
-- Name: wplan_actual; Type: VIEW; Schema: public; Owner: tera
--

CREATE VIEW public.wplan_actual AS
 SELECT DISTINCT ON (wplan_data_unit.field_id, wplan_data_unit.par_id) wplan_data_unit.field_id,
    wplan_data_unit.par_id,
    wplan_data_unit.value_plan
   FROM public.wplan_data_unit
  ORDER BY wplan_data_unit.field_id, wplan_data_unit.par_id, wplan_data_unit.plan_id DESC;


ALTER TABLE public.wplan_actual OWNER TO tera;

--
-- Name: wfraction_dose; Type: VIEW; Schema: public; Owner: tera
--

CREATE VIEW public.wfraction_dose AS
 SELECT c.series_id,
    a.fraction_part_id,
    a.fraction_id,
    a.insert_tms,
    a.value_real AS sh_real,
    e.value_plan AS sh_plan,
    (((c.cumulative_weight * d.totaldose) / ((100 * d.fractionsnumber))::double precision) * ((a.value_real / e.value_plan))::double precision) AS dose
   FROM ((((public.wfraction_data_unit a
     JOIN public.tfraction b USING (fraction_id))
     JOIN public.tfield c ON ((b.field_id = c.field_id)))
     JOIN public.tseries d ON ((c.series_id = d.series_id)))
     JOIN public.wplan_actual e ON (((b.field_id = e.field_id) AND ((a.par_id)::text = (e.par_id)::text))))
  WHERE ((a.par_id)::text = 'SH'::text);


ALTER TABLE public.wfraction_dose OWNER TO tera;

--
-- Name: wpatient; Type: VIEW; Schema: public; Owner: tera
--

CREATE VIEW public.wpatient AS
 SELECT DISTINCT ON (a.patient_id) a.patient_id,
    a.patient_unique_number,
    a.sex,
    a.surname,
    a.forename,
    a.title,
    a.birthdate,
    a.insurance_id,
    a.street,
    a.streetnumber,
    a.city,
    a.postcode,
    a.phone_home,
    a.phone_work,
    a.email,
    a.insert_tms,
    a.insert_user,
    c.series_status_id AS patient_status_id,
    d.name AS status_name,
    b.name AS sex_name
   FROM (((public.tpatient a
     JOIN public.tsex b USING (sex))
     LEFT JOIN public.tseries c USING (patient_id))
     LEFT JOIN public.tseries_status d USING (series_status_id))
  ORDER BY a.patient_id, c.series_status_id;


ALTER TABLE public.wpatient OWNER TO tera;

--
-- Name: wpatientdetail; Type: VIEW; Schema: public; Owner: tera
--

CREATE VIEW public.wpatientdetail AS
 SELECT a.patient_id,
    a.patient_unique_number,
    a.sex,
    a.surname,
    a.forename,
    a.title,
    a.birthdate,
    a.insurance_id,
    a.street,
    a.streetnumber,
    a.city,
    a.postcode,
    a.phone_home,
    a.phone_work,
    a.email,
    a.insert_tms,
    a.insert_user,
    date_part('year'::text, age((a.birthdate)::timestamp with time zone)) AS age,
    b.id AS insurance_code,
    b.name AS insurance_name
   FROM (public.tpatient a
     LEFT JOIN public.tinsurance b USING (insurance_id));


ALTER TABLE public.wpatientdetail OWNER TO tera;

--
-- Name: wplan_actual1; Type: VIEW; Schema: public; Owner: tera
--

CREATE VIEW public.wplan_actual1 AS
 SELECT DISTINCT ON (tplan.field_id, tplan.par_id) tplan.plan_id,
    tplan.field_id,
    tplan.par_id,
    tplan.value_plan
   FROM public.tplan
  ORDER BY tplan.field_id, tplan.par_id, tplan.plan_id DESC;


ALTER TABLE public.wplan_actual1 OWNER TO tera;

--
-- Name: wseries_dose; Type: VIEW; Schema: public; Owner: tera
--

CREATE VIEW public.wseries_dose AS
 SELECT wfield_dose.series_id,
    sum(wfield_dose.sh_total) AS sh_total,
    sum(wfield_dose.dose) AS dose,
    min(wfield_dose.fraction_count) AS fraction_count
   FROM public.wfield_dose
  GROUP BY wfield_dose.series_id;


ALTER TABLE public.wseries_dose OWNER TO tera;

--
-- Name: wtrtimeplan; Type: VIEW; Schema: public; Owner: tera
--

CREATE VIEW public.wtrtimeplan AS
 SELECT DISTINCT ON (b.field_id) a.series_id,
    a.totaldose,
    a.fractionsnumber,
    b.field_id,
    b.cumulative_weight,
    c.plan_id,
    c.par_id,
    c.value_plan,
    c.insert_tms,
    ((a.totaldose * b.cumulative_weight) / (100)::double precision) AS totalfielddose,
        CASE
            WHEN (a.fractionsnumber = 0) THEN (0)::double precision
            ELSE (((a.totaldose * b.cumulative_weight) / (a.fractionsnumber)::double precision) / (100)::double precision)
        END AS fielddose,
    (c.value_plan * a.fractionsnumber) AS trtime_total
   FROM ((public.tseries a
     JOIN public.tfield b USING (series_id))
     JOIN public.tplan c USING (field_id))
  WHERE ((c.par_id)::text = 'SH'::text)
  ORDER BY b.field_id, c.insert_tms DESC;


ALTER TABLE public.wtrtimeplan OWNER TO tera;

--
-- Name: wtrtimereal; Type: VIEW; Schema: public; Owner: tera
--

CREATE VIEW public.wtrtimereal AS
 SELECT a.field_id,
    count(*) AS fraction_count2,
    sum(
        CASE
            WHEN (c.fraction_status_id = 1) THEN 1
            ELSE 0
        END) AS fraction_count,
    max(a.fraction_order) AS fraction_max,
    max(c.insert_tms) AS lasttreated,
    sum(b.value_real) AS trtime_sum
   FROM ((public.tfraction a
     JOIN public.tfraction_part c USING (fraction_id))
     JOIN public.tfraction_data b USING (fraction_part_id))
  WHERE ((a.fraction_type_id = 1) AND ((b.par_id)::text = 'SH'::text))
  GROUP BY a.field_id;


ALTER TABLE public.wtrtimereal OWNER TO tera;

--
-- Name: wtreatment; Type: VIEW; Schema: public; Owner: tera
--

CREATE VIEW public.wtreatment AS
 SELECT wtrtimeplan.field_id,
    wtrtimeplan.series_id,
    wtrtimeplan.totaldose,
    wtrtimeplan.fractionsnumber,
    wtrtimeplan.cumulative_weight,
    wtrtimeplan.plan_id,
    wtrtimeplan.par_id,
    wtrtimeplan.value_plan,
    wtrtimeplan.insert_tms,
    wtrtimeplan.totalfielddose,
    wtrtimeplan.fielddose,
    wtrtimeplan.trtime_total,
    wtrtimereal.fraction_count2,
    wtrtimereal.fraction_count,
    wtrtimereal.fraction_max,
    wtrtimereal.lasttreated,
    wtrtimereal.trtime_sum,
    (((wtrtimereal.trtime_sum)::double precision / (wtrtimeplan.trtime_total)::double precision) * wtrtimeplan.totalfielddose) AS dose_real
   FROM (public.wtrtimeplan
     JOIN public.wtrtimereal USING (field_id));


ALTER TABLE public.wtreatment OWNER TO tera;

--
-- Name: group_id; Type: DEFAULT; Schema: diagnosis; Owner: tera
--

ALTER TABLE ONLY diagnosis.tgroup ALTER COLUMN group_id SET DEFAULT nextval('diagnosis.tgroup_group_id_seq'::regclass);


--
-- Name: pk_diagnosis_tdiagnosis; Type: CONSTRAINT; Schema: diagnosis; Owner: tera
--

ALTER TABLE ONLY diagnosis.tdiagnosis
    ADD CONSTRAINT pk_diagnosis_tdiagnosis PRIMARY KEY (diagnosis_id);


--
-- Name: pk_diagnosis_tgroup; Type: CONSTRAINT; Schema: diagnosis; Owner: tera
--

ALTER TABLE ONLY diagnosis.tgroup
    ADD CONSTRAINT pk_diagnosis_tgroup PRIMARY KEY (group_id);


--
-- Name: pk_tcalendar; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tcalendar
    ADD CONSTRAINT pk_tcalendar PRIMARY KEY (calendar_id);


--
-- Name: pk_tcalendar_status; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tcalendar_status
    ADD CONSTRAINT pk_tcalendar_status PRIMARY KEY (calendar_status_id);


--
-- Name: pk_tconfig; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tconfig
    ADD CONSTRAINT pk_tconfig PRIMARY KEY (config_id);


--
-- Name: pk_tdepartment; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tdepartment
    ADD CONSTRAINT pk_tdepartment PRIMARY KEY (department_id);


--
-- Name: pk_tdiagnosis_series; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tdiagnosis_series
    ADD CONSTRAINT pk_tdiagnosis_series PRIMARY KEY (series_id, diagnosis_id);


--
-- Name: pk_teq; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.teq
    ADD CONSTRAINT pk_teq PRIMARY KEY (eq_id);


--
-- Name: pk_teq_par; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.teq_par
    ADD CONSTRAINT pk_teq_par PRIMARY KEY (eq_id, par_id);


--
-- Name: pk_teq_type; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.teq_type
    ADD CONSTRAINT pk_teq_type PRIMARY KEY (eq_type_id);


--
-- Name: pk_tfield; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tfield
    ADD CONSTRAINT pk_tfield PRIMARY KEY (field_id);


--
-- Name: pk_tfraction; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tfraction
    ADD CONSTRAINT pk_tfraction PRIMARY KEY (fraction_id);


--
-- Name: pk_tfraction_data; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tfraction_data
    ADD CONSTRAINT pk_tfraction_data PRIMARY KEY (fraction_part_id, par_id);


--
-- Name: pk_tfraction_part; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tfraction_part
    ADD CONSTRAINT pk_tfraction_part PRIMARY KEY (fraction_part_id);


--
-- Name: pk_tfraction_status; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tfraction_status
    ADD CONSTRAINT pk_tfraction_status PRIMARY KEY (fraction_status_id);


--
-- Name: pk_tfraction_type; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tfraction_type
    ADD CONSTRAINT pk_tfraction_type PRIMARY KEY (fraction_type_id);


--
-- Name: pk_tholiday; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tholiday
    ADD CONSTRAINT pk_tholiday PRIMARY KEY (hdate);


--
-- Name: pk_timage; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.timage
    ADD CONSTRAINT pk_timage PRIMARY KEY (image_id);


--
-- Name: pk_timport; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.timport
    ADD CONSTRAINT pk_timport PRIMARY KEY (import_id);


--
-- Name: pk_timport_par_map; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.timport_par_map
    ADD CONSTRAINT pk_timport_par_map PRIMARY KEY (importcreator_id, eq_id, par_id, oldval);


--
-- Name: pk_timportcreator; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.timportcreator
    ADD CONSTRAINT pk_timportcreator PRIMARY KEY (importcreator_id);


--
-- Name: pk_tinsurance; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tinsurance
    ADD CONSTRAINT pk_tinsurance PRIMARY KEY (insurance_id);


--
-- Name: pk_tlog; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tlog
    ADD CONSTRAINT pk_tlog PRIMARY KEY (log_id);


--
-- Name: pk_tlog_status; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tlog_status
    ADD CONSTRAINT pk_tlog_status PRIMARY KEY (log_status_id);


--
-- Name: pk_tlog_type; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tlog_type
    ADD CONSTRAINT pk_tlog_type PRIMARY KEY (log_type_id);


--
-- Name: pk_tpar; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tpar
    ADD CONSTRAINT pk_tpar PRIMARY KEY (par_id);


--
-- Name: pk_tpar_gr; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tpar_gr
    ADD CONSTRAINT pk_tpar_gr PRIMARY KEY (par_gr_id);


--
-- Name: pk_tpar_type; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tpar_type
    ADD CONSTRAINT pk_tpar_type PRIMARY KEY (par_type_id);


--
-- Name: pk_tpatient; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tpatient
    ADD CONSTRAINT pk_tpatient PRIMARY KEY (patient_id);


--
-- Name: pk_tplan; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tplan
    ADD CONSTRAINT pk_tplan PRIMARY KEY (plan_id);


--
-- Name: pk_tplan_item; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tplan_item
    ADD CONSTRAINT pk_tplan_item PRIMARY KEY (plan_item_id);


--
-- Name: pk_tsequence; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tsequence
    ADD CONSTRAINT pk_tsequence PRIMARY KEY (sequence_id);


--
-- Name: pk_tsequence_item; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tsequence_item
    ADD CONSTRAINT pk_tsequence_item PRIMARY KEY (sequence_item_id);


--
-- Name: pk_tsequence_name; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tsequence_name
    ADD CONSTRAINT pk_tsequence_name PRIMARY KEY (sequence_name_id);


--
-- Name: pk_tseries; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tseries
    ADD CONSTRAINT pk_tseries PRIMARY KEY (series_id);


--
-- Name: pk_tseries_status; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tseries_status
    ADD CONSTRAINT pk_tseries_status PRIMARY KEY (series_status_id);


--
-- Name: pk_tsex; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tsex
    ADD CONSTRAINT pk_tsex PRIMARY KEY (sex);


--
-- Name: pk_ttech_par_ver; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.ttech_par_ver
    ADD CONSTRAINT pk_ttech_par_ver PRIMARY KEY (eq_id, par_id, tech_id);


--
-- Name: pk_ttechnique; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.ttechnique
    ADD CONSTRAINT pk_ttechnique PRIMARY KEY (tech_id);


--
-- Name: pk_ttol; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.ttol
    ADD CONSTRAINT pk_ttol PRIMARY KEY (tol_id);


--
-- Name: pk_ttol_value; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.ttol_value
    ADD CONSTRAINT pk_ttol_value PRIMARY KEY (tol_id, par_id);


--
-- Name: pk_tunits; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tunits
    ADD CONSTRAINT pk_tunits PRIMARY KEY (unit_id);


--
-- Name: pk_tuser; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tuser
    ADD CONSTRAINT pk_tuser PRIMARY KEY (user_id);


--
-- Name: pk_tuser_type; Type: CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tuser_type
    ADD CONSTRAINT pk_tuser_type PRIMARY KEY (user_type_id);


--
-- Name: creator_eq_idx; Type: INDEX; Schema: public; Owner: tera
--

CREATE UNIQUE INDEX creator_eq_idx ON public.timport USING btree (importcreator_id, treatmentmachinename);


--
-- Name: idx_field_id_order; Type: INDEX; Schema: public; Owner: tera
--

CREATE UNIQUE INDEX idx_field_id_order ON public.tfraction USING btree (fraction_type_id, field_id, fraction_order);


--
-- Name: idx_tuser_login; Type: INDEX; Schema: public; Owner: tera
--

CREATE UNIQUE INDEX idx_tuser_login ON public.tuser USING btree (login);


--
-- Name: instncecreatoruid_idx; Type: INDEX; Schema: public; Owner: tera
--

CREATE UNIQUE INDEX instncecreatoruid_idx ON public.timportcreator USING btree (instancecreatoruid);


--
-- Name: manufacturer_idx; Type: INDEX; Schema: public; Owner: tera
--

CREATE UNIQUE INDEX manufacturer_idx ON public.timportcreator USING btree (manufacturer, manufacturermodelname, softwareversion);


--
-- Name: patient_unique_number_idx; Type: INDEX; Schema: public; Owner: tera
--

CREATE UNIQUE INDEX patient_unique_number_idx ON public.tpatient USING btree (patient_unique_number);


--
-- Name: sequence_iten_idx; Type: INDEX; Schema: public; Owner: tera
--

CREATE UNIQUE INDEX sequence_iten_idx ON public.tsequence_item USING btree (sequence_id, row_id, name);


--
-- Name: sopinstanceuid_idx; Type: INDEX; Schema: public; Owner: tera
--

CREATE UNIQUE INDEX sopinstanceuid_idx ON public.tseries USING btree (sopinstanceuid);


--
-- Name: tfield_series_id_idx; Type: INDEX; Schema: public; Owner: tera
--

CREATE INDEX tfield_series_id_idx ON public.tfield USING btree (series_id);


--
-- Name: tfraction_data_sh_idx; Type: INDEX; Schema: public; Owner: tera
--

CREATE INDEX tfraction_data_sh_idx ON public.tfraction_data USING btree (par_id) WHERE ((par_id)::text = 'SH'::text);


--
-- Name: tfraction_tplan_sh_idx; Type: INDEX; Schema: public; Owner: tera
--

CREATE INDEX tfraction_tplan_sh_idx ON public.tplan USING btree (par_id) WHERE ((par_id)::text = 'SH'::text);


--
-- Name: tlog_status_name_idx; Type: INDEX; Schema: public; Owner: tera
--

CREATE INDEX tlog_status_name_idx ON public.tlog_status USING btree (name);


--
-- Name: tlog_type_name_idx; Type: INDEX; Schema: public; Owner: tera
--

CREATE INDEX tlog_type_name_idx ON public.tlog_type USING btree (name);


--
-- Name: tplan_field_id_idx; Type: INDEX; Schema: public; Owner: tera
--

CREATE INDEX tplan_field_id_idx ON public.tplan USING btree (field_id);


--
-- Name: tplan_field_id_insert_tms_idx; Type: INDEX; Schema: public; Owner: tera
--

CREATE INDEX tplan_field_id_insert_tms_idx ON public.tplan USING btree (field_id, insert_tms);


--
-- Name: tseries_patient_id_idx; Type: INDEX; Schema: public; Owner: tera
--

CREATE INDEX tseries_patient_id_idx ON public.tseries USING btree (patient_id);


--
-- Name: insert_tplan_trig; Type: TRIGGER; Schema: public; Owner: tera
--

CREATE TRIGGER insert_tplan_trig AFTER INSERT ON public.tplan FOR EACH ROW EXECUTE PROCEDURE public.update_exportseriesuid();


--
-- Name: insert_ttol_value_trig; Type: TRIGGER; Schema: public; Owner: tera
--

CREATE TRIGGER insert_ttol_value_trig BEFORE INSERT ON public.ttol_value FOR EACH ROW EXECUTE PROCEDURE public.insert_ttol_value();


--
-- Name: removeloid; Type: TRIGGER; Schema: public; Owner: tera
--

CREATE TRIGGER removeloid BEFORE DELETE ON public.timage FOR EACH ROW EXECUTE PROCEDURE public.removelargeobject();


--
-- Name: series_status_trigger; Type: TRIGGER; Schema: public; Owner: tera
--

CREATE TRIGGER series_status_trigger AFTER UPDATE OF series_status_id ON public.tseries FOR EACH ROW EXECUTE PROCEDURE public.notify_series_change();


--
-- Name: update_tfield_trig; Type: TRIGGER; Schema: public; Owner: tera
--

CREATE TRIGGER update_tfield_trig AFTER UPDATE ON public.tfield FOR EACH ROW EXECUTE PROCEDURE public.update_exportseriesuid();


--
-- Name: update_tplan_item_trig; Type: TRIGGER; Schema: public; Owner: tera
--

CREATE TRIGGER update_tplan_item_trig AFTER UPDATE ON public.tplan_item FOR EACH ROW EXECUTE PROCEDURE public.update_exportseriesuid();


--
-- Name: update_tsequence_item_trig; Type: TRIGGER; Schema: public; Owner: tera
--

CREATE TRIGGER update_tsequence_item_trig AFTER INSERT OR UPDATE ON public.tsequence_item FOR EACH ROW EXECUTE PROCEDURE public.update_exportseriesuid();


--
-- Name: update_tseries_trig; Type: TRIGGER; Schema: public; Owner: tera
--

CREATE TRIGGER update_tseries_trig AFTER UPDATE ON public.tseries FOR EACH ROW EXECUTE PROCEDURE public.update_exportseriesuid();


--
-- Name: diagnosistdiagnosis_fk_group_id; Type: FK CONSTRAINT; Schema: diagnosis; Owner: tera
--

ALTER TABLE ONLY diagnosis.tdiagnosis
    ADD CONSTRAINT diagnosistdiagnosis_fk_group_id FOREIGN KEY (group_id) REFERENCES diagnosis.tgroup(group_id);


--
-- Name: tcalendar_fk_calendar_status_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tcalendar
    ADD CONSTRAINT tcalendar_fk_calendar_status_id FOREIGN KEY (calendar_status_id) REFERENCES public.tcalendar_status(calendar_status_id);


--
-- Name: tcalendar_fk_eq_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tcalendar
    ADD CONSTRAINT tcalendar_fk_eq_id FOREIGN KEY (eq_id) REFERENCES public.teq(eq_id);


--
-- Name: tcalendar_fk_series_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tcalendar
    ADD CONSTRAINT tcalendar_fk_series_id FOREIGN KEY (series_id) REFERENCES public.tseries(series_id) ON DELETE CASCADE;


--
-- Name: tdiagnosis_series_fk_diagnosis_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tdiagnosis_series
    ADD CONSTRAINT tdiagnosis_series_fk_diagnosis_id FOREIGN KEY (diagnosis_id) REFERENCES diagnosis.tdiagnosis(diagnosis_id);


--
-- Name: tdiagnosis_series_fk_series_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tdiagnosis_series
    ADD CONSTRAINT tdiagnosis_series_fk_series_id FOREIGN KEY (series_id) REFERENCES public.tseries(series_id) ON DELETE CASCADE;


--
-- Name: teq_fk_eq_type_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.teq
    ADD CONSTRAINT teq_fk_eq_type_id FOREIGN KEY (eq_type_id) REFERENCES public.teq_type(eq_type_id);


--
-- Name: teq_par_fk_eq_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.teq_par
    ADD CONSTRAINT teq_par_fk_eq_id FOREIGN KEY (eq_id) REFERENCES public.teq(eq_id);


--
-- Name: teq_par_fk_par_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.teq_par
    ADD CONSTRAINT teq_par_fk_par_id FOREIGN KEY (par_id) REFERENCES public.tpar(par_id);


--
-- Name: teq_par_fk_unit_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.teq_par
    ADD CONSTRAINT teq_par_fk_unit_id FOREIGN KEY (unit_id) REFERENCES public.tunits(unit_id);


--
-- Name: tfield_fk_eq_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tfield
    ADD CONSTRAINT tfield_fk_eq_id FOREIGN KEY (eq_id) REFERENCES public.teq(eq_id);


--
-- Name: tfield_fk_series_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tfield
    ADD CONSTRAINT tfield_fk_series_id FOREIGN KEY (series_id) REFERENCES public.tseries(series_id) ON DELETE CASCADE;


--
-- Name: tfield_fk_tech_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tfield
    ADD CONSTRAINT tfield_fk_tech_id FOREIGN KEY (tech_id) REFERENCES public.ttechnique(tech_id);


--
-- Name: tfield_fk_tol_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tfield
    ADD CONSTRAINT tfield_fk_tol_id FOREIGN KEY (tol_id) REFERENCES public.ttol(tol_id);


--
-- Name: tfraction_data_fk_fraction_part_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tfraction_data
    ADD CONSTRAINT tfraction_data_fk_fraction_part_id FOREIGN KEY (fraction_part_id) REFERENCES public.tfraction_part(fraction_part_id) ON DELETE CASCADE;


--
-- Name: tfraction_data_fk_par_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tfraction_data
    ADD CONSTRAINT tfraction_data_fk_par_id FOREIGN KEY (par_id) REFERENCES public.tpar(par_id);


--
-- Name: tfraction_fk_field_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tfraction
    ADD CONSTRAINT tfraction_fk_field_id FOREIGN KEY (field_id) REFERENCES public.tfield(field_id) ON DELETE CASCADE;


--
-- Name: tfraction_fk_fraction_type_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tfraction
    ADD CONSTRAINT tfraction_fk_fraction_type_id FOREIGN KEY (fraction_type_id) REFERENCES public.tfraction_type(fraction_type_id);


--
-- Name: tfraction_part_fk_fraction_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tfraction_part
    ADD CONSTRAINT tfraction_part_fk_fraction_id FOREIGN KEY (fraction_id) REFERENCES public.tfraction(fraction_id);


--
-- Name: tfraction_part_fk_fraction_status_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tfraction_part
    ADD CONSTRAINT tfraction_part_fk_fraction_status_id FOREIGN KEY (fraction_status_id) REFERENCES public.tfraction_status(fraction_status_id);


--
-- Name: timport_fk_eq_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.timport
    ADD CONSTRAINT timport_fk_eq_id FOREIGN KEY (eq_id) REFERENCES public.teq(eq_id);


--
-- Name: timport_fk_importcreator_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.timport
    ADD CONSTRAINT timport_fk_importcreator_id FOREIGN KEY (importcreator_id) REFERENCES public.timportcreator(importcreator_id);


--
-- Name: timport_par_map_fk_eq_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.timport_par_map
    ADD CONSTRAINT timport_par_map_fk_eq_id FOREIGN KEY (eq_id, par_id) REFERENCES public.teq_par(eq_id, par_id);


--
-- Name: timport_par_map_fk_importcreator_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.timport_par_map
    ADD CONSTRAINT timport_par_map_fk_importcreator_id FOREIGN KEY (importcreator_id) REFERENCES public.timportcreator(importcreator_id) ON DELETE CASCADE;


--
-- Name: tlog_fk_log_status_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tlog
    ADD CONSTRAINT tlog_fk_log_status_id FOREIGN KEY (log_status_id) REFERENCES public.tlog_status(log_status_id);


--
-- Name: tlog_fk_log_type_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tlog
    ADD CONSTRAINT tlog_fk_log_type_id FOREIGN KEY (log_type_id) REFERENCES public.tlog_type(log_type_id);


--
-- Name: tpar_fk_par_gr_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tpar
    ADD CONSTRAINT tpar_fk_par_gr_id FOREIGN KEY (par_gr_id) REFERENCES public.tpar_gr(par_gr_id);


--
-- Name: tpar_fk_par_type_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tpar
    ADD CONSTRAINT tpar_fk_par_type_id FOREIGN KEY (par_type_id) REFERENCES public.tpar_type(par_type_id);


--
-- Name: tpatient_fk_insurance_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tpatient
    ADD CONSTRAINT tpatient_fk_insurance_id FOREIGN KEY (insurance_id) REFERENCES public.tinsurance(insurance_id);


--
-- Name: tpatient_fk_sex; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tpatient
    ADD CONSTRAINT tpatient_fk_sex FOREIGN KEY (sex) REFERENCES public.tsex(sex);


--
-- Name: tpatient_tseries; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tseries
    ADD CONSTRAINT tpatient_tseries FOREIGN KEY (patient_id) REFERENCES public.tpatient(patient_id);


--
-- Name: tplan_fk_field_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tplan
    ADD CONSTRAINT tplan_fk_field_id FOREIGN KEY (field_id) REFERENCES public.tfield(field_id) ON DELETE CASCADE;


--
-- Name: tplan_item_fk_plan_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tplan_item
    ADD CONSTRAINT tplan_item_fk_plan_id FOREIGN KEY (plan_id) REFERENCES public.tplan(plan_id) ON DELETE CASCADE;


--
-- Name: tsequence_fk_sequence_name_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tsequence
    ADD CONSTRAINT tsequence_fk_sequence_name_id FOREIGN KEY (sequence_name_id) REFERENCES public.tsequence_name(sequence_name_id);


--
-- Name: tsequence_item_fk_sequence_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tsequence_item
    ADD CONSTRAINT tsequence_item_fk_sequence_id FOREIGN KEY (sequence_id) REFERENCES public.tsequence(sequence_id) ON DELETE CASCADE;


--
-- Name: tseries_fk_importcreator_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tseries
    ADD CONSTRAINT tseries_fk_importcreator_id FOREIGN KEY (importcreator_id) REFERENCES public.timportcreator(importcreator_id);


--
-- Name: tseries_fk_series_status_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tseries
    ADD CONSTRAINT tseries_fk_series_status_id FOREIGN KEY (series_status_id) REFERENCES public.tseries_status(series_status_id);


--
-- Name: ttech_par_ver_fk_eq_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.ttech_par_ver
    ADD CONSTRAINT ttech_par_ver_fk_eq_id FOREIGN KEY (eq_id, par_id) REFERENCES public.teq_par(eq_id, par_id);


--
-- Name: ttech_par_ver_fk_tech_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.ttech_par_ver
    ADD CONSTRAINT ttech_par_ver_fk_tech_id FOREIGN KEY (tech_id) REFERENCES public.ttechnique(tech_id);


--
-- Name: ttol_value_fk_par_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.ttol_value
    ADD CONSTRAINT ttol_value_fk_par_id FOREIGN KEY (par_id) REFERENCES public.tpar(par_id);


--
-- Name: ttol_value_fk_tol_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.ttol_value
    ADD CONSTRAINT ttol_value_fk_tol_id FOREIGN KEY (tol_id) REFERENCES public.ttol(tol_id) ON DELETE CASCADE;


--
-- Name: tunits_fk_par_type_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tunits
    ADD CONSTRAINT tunits_fk_par_type_id FOREIGN KEY (par_type_id) REFERENCES public.tpar_type(par_type_id);


--
-- Name: tuser_fk_department_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tuser
    ADD CONSTRAINT tuser_fk_department_id FOREIGN KEY (department_id) REFERENCES public.tdepartment(department_id);


--
-- Name: tuser_fk_user_type_id; Type: FK CONSTRAINT; Schema: public; Owner: tera
--

ALTER TABLE ONLY public.tuser
    ADD CONSTRAINT tuser_fk_user_type_id FOREIGN KEY (user_type_id) REFERENCES public.tuser_type(user_type_id);


--
-- Name: SCHEMA public; Type: ACL; Schema: -; Owner: postgres
--

REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM postgres;
GRANT ALL ON SCHEMA public TO postgres;
GRANT ALL ON SCHEMA public TO PUBLIC;


--
-- PostgreSQL database dump complete
--

