-- POKECARD SYNC DEFINITIVE DATABASE DDL --

CREATE TABLE sets (
	id VARCHAR NOT NULL, 
	name VARCHAR, 
	series VARCHAR, 
	card_count INTEGER, 
	image_url VARCHAR, 
	release_date VARCHAR, 
	updated_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id)
)

;

CREATE TABLE cards (
	id VARCHAR NOT NULL, 
	name VARCHAR, 
	set_id VARCHAR, 
	image_url VARCHAR, 
	phash VARCHAR, 
	dex_id INTEGER, 
	rarity VARCHAR, 
	category VARCHAR, 
	illustrator VARCHAR, 
	hp INTEGER, 
	types VARCHAR, 
	stage VARCHAR, 
	suffix VARCHAR, 
	attacks VARCHAR, 
	weaknesses VARCHAR, 
	retreat INTEGER, 
	regulation_mark VARCHAR, 
	legal VARCHAR, 
	flavor_text VARCHAR, 
	evolutions VARCHAR, 
	updated_at TIMESTAMP WITHOUT TIME ZONE, 
	last_price_check_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(set_id) REFERENCES sets (id)
)

;

CREATE TABLE card_prices (
	id SERIAL NOT NULL, 
	card_id VARCHAR, 
	price_type VARCHAR, 
	market FLOAT, 
	low FLOAT, 
	mid FLOAT, 
	high FLOAT, 
	direct FLOAT, 
	avg FLOAT, 
	trend FLOAT, 
	trend_1d FLOAT, 
	trend_7d FLOAT, 
	trend_30d FLOAT, 
	updated_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(card_id) REFERENCES cards (id)
)

;

CREATE TABLE change_log (
	version_id SERIAL NOT NULL, 
	card_id VARCHAR, 
	change_type VARCHAR, 
	old_value VARCHAR, 
	new_value VARCHAR, 
	created_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (version_id)
)

;

CREATE TABLE sync_log (
	id SERIAL NOT NULL, 
	sync_type VARCHAR, 
	status VARCHAR, 
	started_at TIMESTAMP WITHOUT TIME ZONE, 
	finished_at TIMESTAMP WITHOUT TIME ZONE, 
	cards_processed INTEGER, 
	cards_added INTEGER, 
	sets_added INTEGER, 
	prices_updated INTEGER, 
	errors_count INTEGER, 
	error_details VARCHAR, 
	PRIMARY KEY (id)
)

;
